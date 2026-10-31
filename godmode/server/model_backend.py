"""Neural-model fallback: best-of-N sampling filtered by the Luau verifier.

The checkpoint path comes from the CLI flag (--ckpt) or env GODMODE_CKPT. With
no checkpoint the backend says so instead of pretending. Sampled candidates are
kept only if they pass check_structure AND have no lint findings; the longest
verified candidate wins. A cheap plausibility filter (ASCII, line length, has
Luau statements, not repetitive) also rejects token soup. If none pass, the reply honestly says the model could
not produce verified code.

Prompt styles:
* ``comment`` (default) - the request rendered as Luau comment lines followed by a
  newline, matching the "-- <comment>\\n<code>" documents the model was trained on.
* ``chat`` - the training chat template ``<|user|>{prompt}\\n<|assistant|>``.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from godmode.luau import check_structure, lint

PROMPT_STYLES = ("comment", "chat")
_BOUNDARY = re.compile(r"\n[ \t]*\n(?=\S)")


def render_prompt(request: str, style: str = "comment") -> str:
    if style == "chat":
        return f"<|user|>{request}\n<|assistant|>"
    if style != "comment":
        raise ValueError(f"unknown prompt style {style!r}; expected one of {PROMPT_STYLES}")
    lines = [ln.strip() for ln in request.strip().splitlines() if ln.strip()] or [""]
    return "".join(f"-- {ln}\n" for ln in lines)


def postprocess(text: str, style: str) -> str:
    for special in ("<|eos|>", "<|user|>", "<|assistant|>"):
        text = text.split(special)[0]
    if style == "chat":
        text = re.sub(r"^\s*```(?:luau|lua)?\s*\n", "", text)
        text = text.split("```")[0]
    else:
        m = _BOUNDARY.search(text)
        if m:
            text = text[:m.start()]
    return text.strip("\n").rstrip()


def plausibility_problems(code: str) -> list[str]:
    """Cheap sanity filter on top of the (lenient) structural checker, to reject token soup."""
    problems = []
    if not code.strip():
        return ["empty"]
    non_ascii = sum(1 for ch in code if ord(ch) > 126 and ch not in "\t")
    if non_ascii / len(code) > 0.01:
        problems.append("non-ASCII noise")
    if any(len(line) > 200 for line in code.splitlines()):
        problems.append("line longer than 200 characters")
    if not re.search(r"\b(local|function|return|if|for|while)\b", code) or not re.search(r"[=(]", code):
        problems.append("no recognisable Luau statement")
    words = re.findall(r"[A-Za-z_]+", code)
    if words and len(set(words)) / len(words) < 0.15:
        problems.append("highly repetitive")
    return problems


@dataclass
class ModelResult:
    ok: bool
    message: str
    code: str | None = None
    source: str = "none"
    verification: list[dict] = field(default_factory=list)
    candidates: int = 0
    verified: int = 0


class ModelBackend:
    def __init__(self, ckpt_path: str | None = None, n: int = 4, max_new_tokens: int = 256, temperature: float = 0.8,
                 top_k: int | None = 40, prompt_style: str = "comment", seed: int = 0):
        self.ckpt_path = ckpt_path if ckpt_path is not None else (os.environ.get("GODMODE_CKPT") or None)
        if prompt_style not in PROMPT_STYLES:
            raise ValueError(f"prompt_style must be one of {PROMPT_STYLES}")
        self.n, self.max_new_tokens = max(1, int(n)), int(max_new_tokens)
        self.temperature, self.top_k, self.prompt_style, self.seed = temperature, top_k, prompt_style, seed
        self._model = self._tok = None
        self._error: str | None = None
        self._lock = threading.Lock()

    @property
    def tag(self) -> str:
        return f"model:{self.ckpt_path}" if self.ckpt_path else "none"

    def _load(self) -> bool:
        with self._lock:
            if self._model is not None:
                return True
            if not self.ckpt_path:
                self._error = "no checkpoint configured (start the server with --ckpt PATH or set GODMODE_CKPT)"
                return False
            if not Path(self.ckpt_path).exists():
                self._error = f"checkpoint not found: {self.ckpt_path}"
                return False
            try:
                from godmode.train import load_checkpoint

                self._model, self._tok, _ = load_checkpoint(self.ckpt_path)
                self._model.eval()
                self._error = None
                return True
            except Exception as e:  # report, never crash the server
                self._error = f"failed to load checkpoint {self.ckpt_path}: {e}"
                return False

    def status(self) -> dict:
        if self._model is not None:
            return {"loaded": True, "checkpoint": self.ckpt_path, "prompt_style": self.prompt_style,
                    "detail": f"best-of-{self.n} sampling, verified by check_structure + lint"}
        detail = self._error or ("checkpoint configured; loaded lazily on first use" if self.ckpt_path
                                 else "no checkpoint configured (start the server with --ckpt PATH or set GODMODE_CKPT)")
        return {"loaded": False, "checkpoint": self.ckpt_path, "prompt_style": self.prompt_style, "detail": detail}

    def generate_code(self, request: str) -> ModelResult:
        if not self._load():
            return ModelResult(False, f"The neural model is not available: {self._error}.", source="none")
        import torch

        tok, model = self._tok, self._model
        prompt = render_prompt(request, self.prompt_style)
        ids = tok.encode(prompt, allow_special=True)
        try:
            eos = {tok.token_id("<|eos|>")}
        except Exception:
            eos = set()
        idx = torch.tensor([ids] * self.n, dtype=torch.long)
        gen = torch.Generator().manual_seed(self.seed)
        with torch.no_grad():
            out = model.generate(idx, self.max_new_tokens, temperature=self.temperature, top_k=self.top_k,
                                 stop_ids=eos, generator=gen)
        prompt_len = min(len(ids), model.cfg.max_seq_len - 1)  # generate() keeps only the last tokens
        candidates = []
        for row in out.tolist():
            completion = row[prompt_len:]
            for i, t in enumerate(completion):
                if t in eos:
                    completion = completion[:i]
                    break
            candidates.append(postprocess(tok.decode(completion), self.prompt_style))
        best, best_checks, verified = None, None, 0
        last_checks: list[dict] = []
        for c in candidates:
            if len(c.strip()) < 10:
                continue
            res = check_structure(c)
            findings = lint(c)
            noise = plausibility_problems(c)
            checks = [{"check": "structure:model_candidate", "ok": res.ok, "detail": "; ".join(res.errors[:3]) or "ok"},
                      {"check": "lint:model_candidate", "ok": not findings,
                       "detail": "; ".join(f"{f.rule} line {f.line}" for f in findings[:3]) or "no findings"},
                      {"check": "plausibility:model_candidate", "ok": not noise, "detail": "; ".join(noise) or "ok"}]
            last_checks = checks
            if res.ok and not findings and not noise:
                verified += 1
                if best is None or len(c) > len(best):
                    best, best_checks = c, checks
        if best is None:
            return ModelResult(False, f"The model could not produce verified code: 0 of {len(candidates)} sampled "
                               "candidates passed the structure check, lint and the plausibility filter.", source=self.tag,
                               verification=last_checks, candidates=len(candidates))
        return ModelResult(True, f"{verified} of {len(candidates)} sampled candidates passed verification; showing the "
                           "longest verified one.", code=best, source=self.tag, verification=best_checks or [],
                           candidates=len(candidates), verified=verified)
