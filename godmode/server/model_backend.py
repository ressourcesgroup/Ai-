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
STYLE_CHOICES = PROMPT_STYLES + ("auto",)  # auto: chat for SFT (instruction-tuned) checkpoints, comment otherwise


def is_sft_checkpoint(ckpt: dict) -> bool:
    m = ckpt.get("metrics") or {}
    return bool(m.get("sft") or "val_loss_before" in m)
_BOUNDARY = re.compile(r"\n[ \t]*\n(?=\S)")


def render_prompt(request: str, style: str = "comment") -> str:
    if style == "chat":
        return f"<|bos|><|user|>{request}\n<|assistant|>"  # exactly the SFT training format
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
                 top_k: int | None = 40, prompt_style: str = "auto", seed: int = 0):
        self.ckpt_path = ckpt_path if ckpt_path is not None else (os.environ.get("GODMODE_CKPT") or None)
        if prompt_style not in STYLE_CHOICES:
            raise ValueError(f"prompt_style must be one of {STYLE_CHOICES}")
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

                self._model, self._tok, ckpt = load_checkpoint(self.ckpt_path)
                self._model.eval()
                if self.prompt_style == "auto":
                    self.prompt_style = "chat" if is_sft_checkpoint(ckpt) else "comment"
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

    def _sample(self, ids: list[int], n: int, max_new: int, temperature: float, gen) -> list[list[int]]:
        import torch

        tok, model = self._tok, self._model
        try:
            eos = {tok.token_id("<|eos|>")}
        except Exception:
            eos = set()
        idx = torch.tensor([ids] * n, dtype=torch.long)
        with torch.no_grad():
            out = model.generate(idx, max_new, temperature=temperature, top_k=self.top_k, stop_ids=eos, generator=gen)
        prompt_len = min(len(ids), model.cfg.max_seq_len - 1)  # generate() keeps only the last tokens
        rows = []
        for row in out.tolist():
            completion = row[prompt_len:]
            for i, t in enumerate(completion):
                if t in eos:
                    completion = completion[:i]
                    break
            rows.append(completion)
        return rows

    def _mean_logprob(self, ids: list[int], completion: str) -> float:
        """Mean log-likelihood the model assigns to a candidate (temperature 1) - used for re-ranking."""
        import torch

        tok, model = self._tok, self._model
        body = tok.encode(completion)
        seq = (ids + body)[-model.cfg.max_seq_len:]
        n_body = min(len(body), len(seq) - 1)
        if n_body <= 0:
            return float("-inf")
        x = torch.tensor([seq[:-1]], dtype=torch.long)
        with torch.no_grad():
            logp = model(x).logits[0].float().log_softmax(-1)
        tgt = torch.tensor(seq[1:])
        lp = logp[torch.arange(len(tgt)), tgt][-n_body:]
        return float(lp.mean())

    def generate_code(self, request: str, level=None, trace=None) -> ModelResult:
        """Best-of-N sampling + verification. ``level`` (a ThinkingLevel) sets N, budget, temperatures and ranking."""
        if not self._load():
            if trace is not None:
                trace.step("Load model", f"unavailable: {self._error}")
            return ModelResult(False, f"The neural model is not available: {self._error}.", source="none")
        import torch

        tok, model = self._tok, self._model
        n = level.samples if level else self.n
        max_new = level.max_new_tokens if level else self.max_new_tokens
        temps = tuple(level.temperatures) if level else (self.temperature,)
        rerank = bool(level and level.rerank)
        prompt = render_prompt(request, self.prompt_style)
        ids = tok.encode(prompt, allow_special=True)
        gen = torch.Generator().manual_seed(self.seed)
        groups: dict[float, int] = {}
        for i in range(n):
            groups[temps[i % len(temps)]] = groups.get(temps[i % len(temps)], 0) + 1
        candidates = []
        for t, k in groups.items():
            candidates += [postprocess(tok.decode(c), self.prompt_style) for c in self._sample(ids, k, max_new, t, gen)]
        if trace is not None:
            trace.step("Sample candidates", f"{len(candidates)} candidate(s), up to {max_new} tokens each, temperature "
                       + "/".join(f"{t:g}" for t in groups))
        verified_list: list[tuple[str, list[dict]]] = []
        last_checks: list[dict] = []
        reasons: dict[str, int] = {}
        for c in candidates:
            if len(c.strip()) < 10:
                reasons["empty"] = reasons.get("empty", 0) + 1
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
                verified_list.append((c, checks))
            else:
                key = "structure" if not res.ok else ("lint" if findings else "plausibility")
                reasons[key] = reasons.get(key, 0) + 1
        verified = len(verified_list)
        if trace is not None:
            why = ", ".join(f"{v} failed {k}" for k, v in reasons.items())
            trace.step("Verify", f"{verified}/{len(candidates)} passed structure + lint + plausibility" + (f" ({why})" if why else ""))
        if not verified_list:
            return ModelResult(False, f"The model could not produce verified code: 0 of {len(candidates)} sampled "
                               "candidates passed the structure check, lint and the plausibility filter.", source=self.tag,
                               verification=last_checks, candidates=len(candidates))
        if rerank and verified > 1:
            scored = [(self._mean_logprob(ids, c), c, ch) for c, ch in verified_list]
            scored.sort(key=lambda r: r[0], reverse=True)
            best, best_checks = scored[0][1], scored[0][2]
            how = "highest model likelihood"
            if trace is not None:
                trace.step("Re-rank", f"scored {verified} verified candidates by mean log-likelihood; best "
                           f"{scored[0][0]:.3f}, worst {scored[-1][0]:.3f}")
        else:
            best, best_checks = max(verified_list, key=lambda r: len(r[0]))
            how = "longest"
            if trace is not None and verified > 1:
                trace.step("Pick", "chose the longest verified candidate")
        return ModelResult(True, f"{verified} of {len(candidates)} sampled candidates passed verification; showing the "
                           f"{how} verified one.", code=best, source=self.tag, verification=best_checks or [],
                           candidates=len(candidates), verified=verified)


_PLACEMENT = re.compile(r"^--\s*(Script|LocalScript|ModuleScript) in ([A-Za-z][\w.]*)\s*:?\s*(.*)$")
_ALLOWED_PARENTS = {
    "Script": {"ServerScriptService", "Workspace", "ServerStorage"},
    "LocalScript": {"StarterPlayer.StarterPlayerScripts", "StarterPlayer.StarterCharacterScripts", "StarterGui",
                    "ReplicatedFirst"},
    "ModuleScript": {"ReplicatedStorage", "ServerScriptService", "ServerStorage"},
}


def parse_placement(code: str) -> tuple[str, str, str] | None:
    """Read the ``-- Script in ServerScriptService: <what>`` header the SFT model is trained to emit.

    Returns (class, parent_path, name) only for a known, sensible class/parent pair; otherwise None.
    """
    first = code.lstrip().split("\n", 1)[0].strip()
    m = _PLACEMENT.match(first)
    if not m:
        return None
    cls, parent, what = m.group(1), m.group(2), m.group(3)
    if parent not in _ALLOWED_PARENTS[cls]:
        return None
    words = re.findall(r"[A-Za-z0-9]+", what)[:4]
    name = "".join(w[:1].upper() + w[1:] for w in words) or "GodmodeScript"
    return cls, parent, name[:40]
