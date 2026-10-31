"""Thinking levels: each level buys a concrete amount of extra compute; nothing is decorative.

What "thinking" means here (honestly): GODMODE's model does not produce hidden chain-of-thought.
More thinking = more sampled candidates (best-of-N), a longer generation budget, a wider
temperature spread, likelihood re-ranking of the verified candidates, and more retrieved
examples. Every step that actually ran is reported back as a trace with its wall time.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

DEFAULT_LEVEL = "medium"


@dataclass(frozen=True)
class ThinkingLevel:
    id: str
    label: str
    description: str
    samples: int            # best-of-N candidates for the neural model
    max_new_tokens: int     # generation budget per candidate
    temperatures: tuple     # candidates are spread over these temperatures (round-robin)
    rerank: bool            # rank verified candidates by mean token log-likelihood instead of length
    retrieval_k: int        # corpus examples returned for questions

    def to_json(self) -> dict:
        d = asdict(self)
        d["temperatures"] = list(self.temperatures)
        return d


LEVELS: dict[str, ThinkingLevel] = {lv.id: lv for lv in (
    ThinkingLevel("low", "Low", "1 sample, no re-ranking. Fastest.", 1, 384, (0.6,), False, 1),
    ThinkingLevel("medium", "Medium", "Best of 4 verified samples.", 4, 512, (0.7, 0.9), False, 3),
    ThinkingLevel("high", "High", "Best of 8, ranked by model likelihood.", 8, 640, (0.6, 0.8, 1.0), True, 4),
    ThinkingLevel("xhigh", "Extra high", "Best of 16, wider temperature spread, likelihood ranking.", 16, 704,
                  (0.5, 0.7, 0.9, 1.1), True, 5),
    ThinkingLevel("max", "Max", "Best of 32 with the full generation budget. Slowest (can take a minute on CPU).", 32, 736,
                  (0.4, 0.6, 0.8, 1.0, 1.2), True, 6),
)}


def resolve_level(value) -> ThinkingLevel:
    """Accepts an id ('xhigh'), a label ('Extra high'), or None (default). Raises ValueError otherwise."""
    if value is None:
        return LEVELS[DEFAULT_LEVEL]
    if not isinstance(value, str):
        raise ValueError("thinking must be one of " + ", ".join(LEVELS))
    key = value.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    key = {"extrahigh": "xhigh", "maximum": "max", "med": "medium"}.get(key, key)
    if key not in LEVELS:
        raise ValueError("thinking must be one of " + ", ".join(LEVELS))
    return LEVELS[key]


def list_levels() -> list[dict]:
    return [{"id": lv.id, "label": lv.label, "description": lv.description} for lv in LEVELS.values()]


class Trace:
    """Collects the steps that actually ran, each with its measured wall time."""

    def __init__(self, level: ThinkingLevel):
        self.level = level
        self.steps: list[dict] = []
        self._t0 = time.perf_counter()
        self._last = self._t0

    def step(self, label: str, detail: str) -> None:
        now = time.perf_counter()
        self.steps.append({"label": label, "detail": detail, "ms": int((now - self._last) * 1000)})
        self._last = now

    def to_json(self, summary: str) -> dict:
        return {"level": self.level.id, "summary": summary, "elapsed_ms": int((time.perf_counter() - self._t0) * 1000),
                "steps": self.steps}
