"""Per-language evaluation: does the model write the requested script from an English, French or Spanish prompt?

Prompt sets (all freshly generated with an eval seed, so new names, numbers and phrasings):

* ``family_seen`` / ``family_held``: the script families (held = never trained on), scored with
  ``script_eval.score``; ``complete`` is its strict proxy (right class + services + task API).
* ``compose_seen``: new trigger x effect programs whose trigger/effect pairs were trained on.
* ``compose_held``: trigger/effect pairs that never appear together in training (e.g. a click that sets
  speed). Here ``complete`` means: the code verifies AND every check passes (right event wiring, right
  Roblox API, the exact numbers the prompt asked for). The model must assemble this code itself; no
  template for the pair exists in its training data.

These are code-level checks, not playtests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..data import compose
from ..data.script_families import FAMILIES, HELD_OUT_FAMILIES, generate
from ..server.model_backend import ModelBackend
from ..server.thinking import resolve_level
from .script_eval import score

LANGS = ("en", "fr", "es")


def prompts(per_lang: int = 12, seed: int = 4242) -> list[dict]:
    out = []
    seen_f = [f for f in FAMILIES if f not in HELD_OUT_FAMILIES]
    for i, lang in enumerate(LANGS):
        fs, _ = generate(1, seed=seed + i, families=seen_f, langs=(lang,))
        fh, _ = generate(max(1, per_lang // 3), seed=seed + 10 + i, families=list(HELD_OUT_FAMILIES), langs=(lang,))
        out += [{"set": "family_seen", "lang": lang, "prompt": s.prompt, "reference": s.solution, "family": s.family}
                for s in fs[:per_lang]]
        out += [{"set": "family_held", "lang": lang, "prompt": s.prompt, "reference": s.solution, "family": s.family}
                for s in fh[:per_lang]]
    for split, name in (("train", "compose_seen"), ("heldout", "compose_held")):
        cs, _ = compose.generate(per_lang * 6, seed=seed + (50 if split == "train" else 60), split=split)
        for lang in LANGS:
            for s in [c for c in cs if c.spec.lang == lang][:per_lang]:
                out.append({"set": name, "lang": lang, "prompt": s.prompt, "reference": s.solution,
                            "family": s.family, "checks": s.checks})
    return out


def grade_row(p: dict, code: str | None) -> dict:
    if p["set"].startswith("compose"):
        g = compose.grade(p["checks"], code or "") if code else {"verified": False, "checks_passed": 0,
                                                                   "checks_total": len(p["checks"]), "missing": [],
                                                                   "correct": False}
        return {"verified": code is not None, "complete": bool(code and g["correct"]),
                "check_rate": round(g["checks_passed"] / max(1, g["checks_total"]), 3), "missing": g["missing"]}
    s = score(code, p["reference"])
    return {"verified": s["verified"], "complete": s["complete"], "check_rate": s["api_recall"]}


def summarize(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        for key in (r["set"], f'{r["set"]}/{r["lang"]}'):
            d = out.setdefault(key, {"n": 0, "verified": 0, "complete": 0, "check_rate": 0.0})
            d["n"] += 1
            d["verified"] += r["verified"]
            d["complete"] += r["complete"]
            d["check_rate"] += r["check_rate"]
    for d in out.values():
        n = d["n"]
        d["verified"], d["complete"], d["check_rate"] = (round(d["verified"] / n, 3), round(d["complete"] / n, 3),
                                                         round(d["check_rate"] / n, 3))
    return dict(sorted(out.items()))


def run(ckpt: str, level: str = "low", per_lang: int = 12, seed: int = 4242, log=print) -> dict:
    backend = ModelBackend(ckpt, seed=0)
    lv = resolve_level(level)
    ps = prompts(per_lang, seed)
    rows = []
    for i, p in enumerate(ps):
        t0 = time.time()
        res = backend.generate_code(p["prompt"], lv)
        code = res.code if res.ok else None
        row = {k: p[k] for k in ("set", "lang", "family", "prompt")}
        row.update(grade_row(p, code), seconds=round(time.time() - t0, 2), code=code)
        rows.append(row)
        log(f"[{i + 1}/{len(ps)}] {p['set']:12s} {p['lang']} complete={row['complete']!s:5s} {p['prompt'][:60]}")
    return {"checkpoint": ckpt, "level": lv.id, "seed": seed, "summary": summarize(rows), "rows": rows}


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--level", default="low")
    ap.add_argument("--per-lang", type=int, default=12)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    r = run(a.ckpt, a.level, a.per_lang, a.seed)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(r, indent=2, ensure_ascii=False))
    print(json.dumps(r["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
