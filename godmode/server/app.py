"""GODMODE local HTTP server (stdlib only). See docs/PLUGIN_PROTOCOL.md.

    python -m godmode.server --port 8765 [--ckpt PATH] [--token T] [--best-of N] [--prompt-style comment|chat]

Binds 127.0.0.1 by default (env GODMODE_HOST, PORT); non-local binds require a token. If a token is configured every request must send
`X-Godmode-Token`. Request bodies are limited to 1 MiB.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path

from .model_backend import STYLE_CHOICES, ModelBackend
from .retrieval import Retriever
from .service import BadRequest, GodmodeService

MAX_BODY = 1 << 20
DRAIN_LIMIT = 8 << 20  # oversized bodies up to this size are read and discarded before replying


class GodmodeHandler(BaseHTTPRequestHandler):
    server_version = "GodmodeServer/0.1"
    protocol_version = "HTTP/1.1"

    # set on the server instance
    @property
    def service(self) -> GodmodeService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # quiet unless verbose
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send(status, {"ok": False, "error": {"code": code, "message": message}})

    def _drain(self, n: int) -> None:
        try:
            self.connection.settimeout(5)
            while n > 0:
                chunk = self.rfile.read(min(n, 65536))
                if not chunk:
                    break
                n -= len(chunk)
        except OSError:
            pass

    def _authorized(self) -> bool:
        token = getattr(self.server, "token", None)
        if not token:
            return True
        got = self.headers.get("X-Godmode-Token", "")
        return hmac.compare_digest(got.encode(), token.encode())

    def _route(self, method: str) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        routes = {"/health": "GET", "/chat": "POST", "/verify": "POST"}
        if not self._authorized():
            # drain a small body so the connection stays sane, then refuse
            self.close_connection = True
            return self._error(401, "unauthorized", "missing or wrong X-Godmode-Token")
        if path not in routes:
            self.close_connection = True
            return self._error(404, "not_found", f"no endpoint {path}")
        if routes[path] != method:
            self.close_connection = True
            return self._error(405, "method_not_allowed", f"{path} expects {routes[path]}")
        try:
            if method == "GET":
                return self._send(200, self.service.health())
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0:
                self.close_connection = True
                return self._error(400, "bad_request", "invalid Content-Length")
            if length > MAX_BODY:
                self.close_connection = True
                self._drain(min(length, DRAIN_LIMIT))  # lets well-behaved clients read the 413
                return self._error(413, "too_large", f"request body exceeds {MAX_BODY} bytes")
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                return self._error(400, "bad_json", f"invalid JSON: {e}")
            if not isinstance(body, dict):
                return self._error(400, "bad_request", "JSON body must be an object")
            handler = self.service.chat if path == "/chat" else self.service.verify
            return self._send(200, handler(body))
        except BadRequest as e:
            return self._error(400, "bad_request", str(e))
        except Exception as e:  # never leak a stack trace to the client
            if getattr(self.server, "verbose", False):
                traceback.print_exc()
            return self._error(500, "internal", f"{type(e).__name__}: {e}")

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_PUT(self):  # noqa: N802
        self._route("PUT")

    def do_DELETE(self):  # noqa: N802
        self._route("DELETE")


def make_server(host: str = "127.0.0.1", port: int = 8765, service: GodmodeService | None = None,
                token: str | None = None, verbose: bool = False) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), GodmodeHandler)
    httpd.daemon_threads = True
    httpd.service = service or GodmodeService()  # type: ignore[attr-defined]
    httpd.token = token or None  # type: ignore[attr-defined]
    httpd.verbose = verbose  # type: ignore[attr-defined]
    return httpd


def serve_in_thread(httpd: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=httpd.serve_forever, name="godmode-server", daemon=True)
    t.start()
    return t


CHECKPOINT_CANDIDATES = ("runs/sft-scripts/best.pt", "runs/small-full2/best.pt")


def default_checkpoint() -> str | None:
    """The instruction-tuned checkpoint if it exists, else the pretrained one, else None (tools still work)."""
    root = Path(__file__).resolve().parents[2]
    for rel in CHECKPOINT_CANDIDATES:
        for base in (Path.cwd(), root):
            if (base / rel).exists():
                return str(base / rel)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m godmode.server", description="GODMODE local inference/agent server")
    ap.add_argument("--host", default=os.environ.get("GODMODE_HOST", "127.0.0.1"),
                    help="bind address (default 127.0.0.1; use 0.0.0.0 only behind a token, e.g. online hosting)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--ckpt", default=os.environ.get("GODMODE_CKPT") or default_checkpoint(),
                    help="model checkpoint (or env GODMODE_CKPT; default: the newest trained checkpoint found)")
    ap.add_argument("--token", default=os.environ.get("GODMODE_TOKEN"), help="require X-Godmode-Token")
    ap.add_argument("--best-of", type=int, default=4, help="model samples per request (best verified wins)")
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--prompt-style", choices=STYLE_CHOICES, default="auto",
                    help="auto = chat format for instruction-tuned (SFT) checkpoints, comment format otherwise")
    ap.add_argument("--corpus", action="append", default=None,
                    help="corpus directory for retrieval (repeatable; default seed + external corpus)")
    ap.add_argument("--allow-no-token", action="store_true", help="allow a non-local bind without a token (unsafe)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    model = ModelBackend(args.ckpt or None, n=args.best_of, max_new_tokens=args.max_new_tokens,
                         temperature=args.temperature, prompt_style=args.prompt_style)
    retriever = Retriever(args.corpus) if args.corpus else Retriever()
    httpd = make_server(args.host, args.port, GodmodeService(model, retriever), args.token, args.verbose)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        if not args.token and not args.allow_no_token:
            httpd.server_close()
            print(f"refusing to bind {args.host} without a token: set GODMODE_TOKEN (or --token), "
                  "or pass --allow-no-token if you really want an open server", file=sys.stderr)
            return 2
        print(f"WARNING: binding to {args.host} exposes the server beyond this machine", file=sys.stderr)
    print(f"GODMODE server on http://{args.host}:{httpd.server_address[1]} "
          f"(model: {model.tag}; token: {'on' if args.token else 'off'})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
