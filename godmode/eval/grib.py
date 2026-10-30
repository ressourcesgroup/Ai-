"""GODMODE Roblox Intelligence Benchmark (GRIB) harness, v0.

v0 grading is STATIC: structure check, security lint and pattern assertions.
It cannot tell whether code behaves correctly at runtime. Execution-based
grading (Luau runtime + Roblox API mocks) is the next GRIB milestone; see
docs/STATUS.md. Scores from v0 must be reported as "GRIB-v0 static".

Every model, including competitors, is called through the same `Adapter`
signature with the same prompt, so comparisons are like-for-like.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ..luau import check_structure, lint

Adapter = Callable[[str], str]

_FENCE = re.compile(r"```(?:lua|luau)?\s*\n(.*?)```", re.S)


@dataclass
class Task:
    id: str
    tier: str  # beginner | intermediate | advanced | expert
    category: str
    prompt: str
    checks: list[dict]
    reference: str

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(**d)


@dataclass
class TaskResult:
    id: str
    tier: str
    category: str
    passed: bool
    failed_checks: list[str] = field(default_factory=list)
    seconds: float = 0.0
    security_failure: bool = False


def load_tasks(path: str | Path) -> list[Task]:
    tasks = [Task.from_dict(json.loads(line)) for line in Path(path).read_text().splitlines() if line.strip()]
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate GRIB task ids")
    return tasks


def extract_code(response: str) -> str:
    blocks = _FENCE.findall(response)
    return "\n".join(blocks) if blocks else response


def run_check(code: str, check: dict) -> tuple[bool, str]:
    kind = check["type"]
    if kind == "structure":
        r = check_structure(code)
        return r.ok, "structure: " + "; ".join(r.errors[:2])
    if kind == "lint_clean":
        rules = set(check.get("rules", []))
        # GM000 (lexical error) is a structure failure, not a security failure.
        bad = [f for f in lint(code) if f.rule != "GM000" and (not rules or f.rule in rules)]
        return not bad, "lint: " + ", ".join(f"{f.rule}@{f.line}" for f in bad)
    if kind == "contains":
        return re.search(check["pattern"], code, re.M) is not None, f"missing /{check['pattern']}/"
    if kind == "not_contains":
        return re.search(check["pattern"], code, re.M) is None, f"forbidden /{check['pattern']}/"
    raise ValueError(f"unknown check type {kind!r}")


MIN_CODE_TOKENS = 8


def grade(task: Task, response: str) -> TaskResult:
    code = extract_code(response)
    failed, security = [], False
    # Implicit check: tasks with only negative assertions must not be passable
    # by an empty or trivial answer (found by the empty-adapter baseline).
    r = check_structure(code)
    if r.ok and sum(t.kind != "eof" for t in r.tokens) < MIN_CODE_TOKENS:
        failed.append("non_trivial_code")
    for check in task.checks:
        ok, msg = run_check(code, check)
        if not ok:
            failed.append(check.get("name", msg))
            security |= check["type"] == "lint_clean"
    return TaskResult(task.id, task.tier, task.category, not failed, failed, security_failure=security)


def run(tasks: list[Task], adapter: Adapter, model_name: str, contaminated: set[str] | None = None) -> dict:
    """Run every task through `adapter`. `contaminated` task ids are graded but
    excluded from `pass_rate_clean`, the headline metric."""
    contaminated = contaminated or set()
    results = []
    for task in tasks:
        t0 = time.time()
        try:
            response = adapter(task.prompt)
        except Exception as e:  # an adapter crash is a task failure, not a harness failure
            response = f"-- adapter error: {e}"
        r = grade(task, response)
        r.seconds = round(time.time() - t0, 3)
        results.append(r)

    def rate(rs: list[TaskResult]) -> float:
        return round(sum(r.passed for r in rs) / len(rs), 4) if rs else 0.0

    by_tier: dict[str, list[TaskResult]] = {}
    by_cat: dict[str, list[TaskResult]] = {}
    for r in results:
        by_tier.setdefault(r.tier, []).append(r)
        by_cat.setdefault(r.category, []).append(r)
    return {
        "benchmark": "GRIB-v0-static",
        "model": model_name,
        "n_tasks": len(results),
        "pass_rate": rate(results),
        "pass_rate_clean": rate([r for r in results if r.id not in contaminated]),
        "contaminated_tasks": sorted(contaminated),
        "security_failures": sum(r.security_failure for r in results),
        "by_tier": {k: rate(v) for k, v in sorted(by_tier.items())},
        "by_category": {k: rate(v) for k, v in sorted(by_cat.items())},
        "total_seconds": round(sum(r.seconds for r in results), 2),
        "results": [asdict(r) for r in results],
    }


def _ngrams(text: str, n: int) -> set[str]:
    words = re.findall(r"\w+|[^\w\s]", text)
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


CONTAMINATION_THRESHOLD = 0.05


def contaminated_ids(report: dict[str, float], threshold: float = CONTAMINATION_THRESHOLD) -> set[str]:
    return {k for k, v in report.items() if v > threshold}


def contamination_report(tasks: list[Task], training_texts: list[str], n: int = 13) -> dict[str, float]:
    """Fraction of each task's reference n-grams that appear in training data.

    Any nonzero value means the task may be memorized and should be rotated
    out or the overlapping training documents removed.
    """
    train_grams: set[str] = set()
    for t in training_texts:
        train_grams |= _ngrams(t, n)
    report = {}
    for task in tasks:
        grams = _ngrams(task.reference, n)
        report[task.id] = round(len(grams & train_grams) / len(grams), 4) if grams else 0.0
    return report


# ---- built-in adapters -------------------------------------------------

def reference_adapter(tasks: list[Task]) -> Adapter:
    """Oracle that returns each task's reference: validates the harness (must score 100%)."""
    by_prompt = {t.prompt: t.reference for t in tasks}
    return lambda prompt: by_prompt[prompt]


def empty_adapter(prompt: str) -> str:
    """Lower bound: returns nothing."""
    return ""


def checkpoint_adapter(ckpt_path: str, max_new_tokens: int = 256, temperature: float = 0.0) -> Adapter:
    """Adapter for a GODMODE checkpoint, using the chat template from training."""
    import torch

    from ..train import load_checkpoint

    model, tok, _ = load_checkpoint(ckpt_path)
    eos = tok.token_id("<|eos|>")

    def generate(prompt: str) -> str:
        ids = tok.encode(f"<|user|>{prompt}\n<|assistant|>", allow_special=True)
        ids = ids[-(model.cfg.max_seq_len - 16):]
        out = model.generate(torch.tensor([ids]), max_new_tokens, temperature=temperature, stop_ids={eos})
        return tok.decode([i for i in out[0, len(ids):].tolist() if i != eos])

    return generate
