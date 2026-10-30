"""Experiment registry and model promotion gate (continuous-improvement engine v0).

The registry is an append-only JSONL file: every experiment records its
hypothesis, exact configuration, code version, seeds and metrics, so any
result can be reproduced or audited. Records are never edited in place.

The promotion gate encodes the rule that a candidate is promoted only when it
beats the baseline beyond seed noise on the primary metric AND does not regress
on any guard metric. It returns a decision; it never swaps weights itself.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


def git_revision() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@dataclass
class ExperimentRecord:
    name: str
    hypothesis: str
    config: dict
    seeds: list[int]
    metrics: dict
    conclusion: str = ""
    status: str = "completed"  # completed | failed | inconclusive
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    git: str = field(default_factory=git_revision)
    timestamp: float = field(default_factory=time.time)


class Registry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: ExperimentRecord) -> ExperimentRecord:
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(record)) + "\n")
        return record

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def find(self, name: str) -> list[dict]:
        return [r for r in self.all() if r["name"] == name]


@dataclass
class GateDecision:
    promote: bool
    reasons: list[str]


def promotion_gate(
    baseline: dict[str, list[float]],
    candidate: dict[str, list[float]],
    primary: str,
    lower_is_better: dict[str, bool],
    guard_tolerance: float = 0.0,
) -> GateDecision:
    """Decide whether `candidate` may replace `baseline`.

    Each dict maps metric name -> per-seed values. The primary metric must
    improve by more than the pooled seed standard deviation (needs >= 2 seeds
    per side). Every other metric present in both is a guard: it may not get
    worse by more than `guard_tolerance` (absolute, on the mean).
    """
    reasons: list[str] = []
    b, c = baseline.get(primary, []), candidate.get(primary, [])
    if len(b) < 2 or len(c) < 2:
        return GateDecision(False, [f"need >= 2 seeds per side for '{primary}' (got {len(b)} and {len(c)})"])

    sign = -1 if lower_is_better.get(primary, True) else 1
    gain = sign * (statistics.mean(c) - statistics.mean(b))
    noise = statistics.pstdev(b + c)
    ok = gain > noise
    reasons.append(f"{primary}: gain {gain:+.4f} vs noise {noise:.4f} -> {'improved' if ok else 'not significant'}")

    for metric in sorted(set(baseline) & set(candidate) - {primary}):
        s = -1 if lower_is_better.get(metric, True) else 1
        delta = s * (statistics.mean(candidate[metric]) - statistics.mean(baseline[metric]))
        if delta < -guard_tolerance:
            ok = False
            reasons.append(f"guard {metric}: regressed by {-delta:.4f}")
        else:
            reasons.append(f"guard {metric}: ok ({delta:+.4f})")
    return GateDecision(ok, reasons)
