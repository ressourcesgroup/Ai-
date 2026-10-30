"""Experiment registry and model promotion gate (continuous-improvement engine v0).

The registry is an append-only JSONL file: every experiment records its
hypothesis, exact configuration, code version, seeds and metrics, so any
result can be reproduced or audited. Records are never edited in place.

The promotion gate encodes the rule that a candidate is promoted only when it
beats the baseline with statistical significance across seeds on the primary metric AND does not regress
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


# One-sided Student-t critical values at alpha = 0.05, indexed by degrees of freedom.
_T95 = {1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833, 10: 1.812,
        12: 1.782, 15: 1.753, 20: 1.725, 30: 1.697, 60: 1.671}


def _t_critical(df: float) -> float:
    for k in sorted(_T95):
        if df <= k:
            return _T95[k]
    return 1.645


def welch_t(a: list[float], b: list[float]) -> tuple[float, float, float]:
    """Return (mean(a) - mean(b), standard error, Welch-Satterthwaite df)."""
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    se = (va + vb) ** 0.5
    if se == 0:
        return statistics.mean(a) - statistics.mean(b), 0.0, float(len(a) + len(b) - 2)
    df = (va + vb) ** 2 / ((va**2 / (len(a) - 1) if va else 0) + (vb**2 / (len(b) - 1) if vb else 0) or 1e-12)
    return statistics.mean(a) - statistics.mean(b), se, df


def promotion_gate(
    baseline: dict[str, list[float]],
    candidate: dict[str, list[float]],
    primary: str,
    lower_is_better: dict[str, bool],
    guard_tolerance: float = 0.0,
    min_effect: float = 0.0,
) -> GateDecision:
    """Decide whether `candidate` may replace `baseline`.

    Each dict maps metric name -> per-seed values. The primary metric must
    improve by more than `min_effect` AND pass a one-sided Welch t-test at
    alpha = 0.05 (needs >= 2 seeds per side; with zero variance on both sides
    only `min_effect` applies, so set it when seeds are deterministic). Every
    other metric present in both is a guard: its mean may not get worse by
    more than `guard_tolerance` (absolute).
    """
    reasons: list[str] = []
    b, c = baseline.get(primary, []), candidate.get(primary, [])
    if len(b) < 2 or len(c) < 2:
        return GateDecision(False, [f"need >= 2 seeds per side for '{primary}' (got {len(b)} and {len(c)})"])

    sign = -1 if lower_is_better.get(primary, True) else 1
    diff, se, df = welch_t(c, b)
    gain = sign * diff
    if se == 0:
        ok = gain > min_effect
        reasons.append(f"{primary}: gain {gain:+.4f}, zero variance -> {'improved' if ok else 'not improved'} (min_effect {min_effect})")
    else:
        t, crit = gain / se, _t_critical(df)
        ok = t > crit and gain > min_effect
        reasons.append(f"{primary}: gain {gain:+.4f}, t={t:.2f} vs {crit:.2f} (df={df:.1f}) -> {'significant' if ok else 'not significant'}")

    for metric in sorted(set(baseline) & set(candidate) - {primary}):
        s_ = -1 if lower_is_better.get(metric, True) else 1
        delta = s_ * (statistics.mean(candidate[metric]) - statistics.mean(baseline[metric]))
        if delta < -guard_tolerance:
            ok = False
            reasons.append(f"guard {metric}: regressed by {-delta:.4f}")
        else:
            reasons.append(f"guard {metric}: ok ({delta:+.4f})")
    return GateDecision(ok, reasons)
