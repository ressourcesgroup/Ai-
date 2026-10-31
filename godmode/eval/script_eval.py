"""Measure whether a checkpoint writes complete, correctly placed scripts.

For fresh prompts (new seed, so new names/numbers/phrasings) from the training families
("seen") and from families never trained on ("held-out"), run the real server code path
(``ModelBackend.generate_code`` at a thinking level) and record per prompt:

* ``verified``  - a candidate passed structure + lint + plausibility (what the server would return)
* ``placed``    - the returned code starts with a valid placement header
* ``class_ok``  - that header names the same script class as the reference solution
* ``service_recall`` - fraction of the reference's ``game:GetService`` services the code also uses
* ``complete``  - verified and class_ok and service_recall >= 0.5 (a strict, cheap proxy for
  "a whole script for this task", not a proof that it runs correctly in Roblox)

These are proxies measured by code, not playtests. Results are written as JSON.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..data.script_families import FAMILIES, HELD_OUT_FAMILIES, generate
from ..server.model_backend import ModelBackend, parse_placement
from ..server.thinking import resolve_level

_SERVICE = re.compile(r'GetService\(\s*"(\w+)"\s*\)')


def services(code: str) -> set[str]:
    return set(_SERVICE.findall(code or ""))


def score(code: str | None, reference: str) -> dict:
    ref_place = parse_placement(reference)
    place = parse_placement(code) if code else None
    ref_svc = services(reference)
    recall = (len(services(code) & ref_svc) / len(ref_svc)) if (code and ref_svc) else (1.0 if code else 0.0)
    class_ok = bool(place and ref_place and place[0] == ref_place[0])
    return {"verified": code is not None, "placed": place is not None, "class_ok": class_ok,
            "service_recall": round(recall, 3), "complete": bool(code and class_ok and recall >= 0.5)}


def eval_prompts(per_family: int = 1, held_per_family: int = 3, seed: int = 9999) -> list[dict]:
    seen = [f for f in FAMILIES if f not in HELD_OUT_FAMILIES]
    a, _ = generate(per_family, seed=seed, families=seen)
    b, _ = generate(held_per_family, seed=seed + 1, families=list(HELD_OUT_FAMILIES))
    return [{"split": "seen", "family": s.family, "prompt": s.prompt, "reference": s.solution} for s in a] + \
           [{"split": "held_out", "family": s.family, "prompt": s.prompt, "reference": s.solution} for s in b]


def summarize(rows: list[dict]) -> dict:
    out = {}
    for split in ("seen", "held_out"):
        rs = [r for r in rows if r["split"] == split]
        if not rs:
            continue
        n = len(rs)
        out[split] = {"n": n, **{k: round(sum(r[k] for r in rs) / n, 3)
                                 for k in ("verified", "placed", "class_ok", "complete", "service_recall")},
                      "mean_seconds": round(sum(r["seconds"] for r in rs) / n, 2)}
    return out


def run(ckpt: str, level: str = "low", per_family: int = 1, held_per_family: int = 3, seed: int = 9999,
        limit: int | None = None, log=print) -> dict:
    backend = ModelBackend(ckpt, seed=0)
    lv = resolve_level(level)
    prompts = eval_prompts(per_family, held_per_family, seed)
    if limit:
        prompts = prompts[:limit]
    rows = []
    for i, p in enumerate(prompts):
        t0 = time.time()
        res = backend.generate_code(p["prompt"], lv)
        row = {"split": p["split"], "family": p["family"], "prompt": p["prompt"], "seconds": round(time.time() - t0, 2),
               **score(res.code if res.ok else None, p["reference"]), "candidates": res.candidates,
               "verified_candidates": res.verified, "code": res.code}
        rows.append(row)
        log(f"[{i + 1}/{len(prompts)}] {p['split']:8s} {p['family']:24s} verified={row['verified']!s:5s} "
            f"complete={row['complete']!s:5s} {row['seconds']}s")
    return {"checkpoint": ckpt, "prompt_style": backend.prompt_style, "level": lv.id, "seed": seed,
            "summary": summarize(rows), "rows": rows}


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--level", default="low")
    ap.add_argument("--per-family", type=int, default=1)
    ap.add_argument("--held-per-family", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    r = run(a.ckpt, a.level, a.per_family, a.held_per_family, limit=a.limit)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(r, indent=2))
    print(json.dumps(r["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
