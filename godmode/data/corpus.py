"""Corpus ingestion: provenance gating, quality filtering, deduplication.

Every source directory must contain a SOURCE.json declaring its license. Files
from directories without one are refused: the project only trains on data
whose legal status is documented (docs/ARCHITECTURE.md §4.1).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from ..luau import LuauSyntaxError, check_structure, lint, tokenize

ALLOWED_LICENSES = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "Unlicense", "CC-BY-4.0"})
LUAU_EXTENSIONS = (".luau", ".lua")


@dataclass
class Document:
    text: str
    source: str
    license: str
    path: str
    kind: str = "luau"
    meta: dict = field(default_factory=dict)

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@dataclass
class FilterReport:
    accepted: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def load_source_dir(directory: str | Path) -> list[Document]:
    directory = Path(directory)
    meta_path = directory / "SOURCE.json"
    if not meta_path.exists():
        raise PermissionError(f"{directory} has no SOURCE.json; refusing to ingest data of unknown provenance")
    meta = json.loads(meta_path.read_text())
    lic = meta.get("license")
    if lic not in ALLOWED_LICENSES:
        raise PermissionError(f"{directory}: license {lic!r} is not on the allow-list {sorted(ALLOWED_LICENSES)}")
    docs = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in LUAU_EXTENSIONS:
            docs.append(Document(path.read_text(encoding="utf-8"), meta["name"], lic, str(path)))
        elif path.suffix == ".jsonl" and meta.get("format") == "prompt_completion":
            # Fragment datasets: the prompt (usually a comment) followed by its completion is one document.
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                if line.strip():
                    rec = json.loads(line)
                    docs.append(Document(rec["prompt"] + rec["completion"], meta["name"], lic, f"{path}#{i}",
                                         meta={"upstream_split": path.stem}))
    return docs


def normalize_for_dedup(text: str) -> str:
    """Comment- and whitespace-insensitive form used for duplicate detection.

    Uses the Luau lexer so `--` inside strings is not mistaken for a comment;
    falls back to whitespace normalization for text the lexer rejects (prose,
    chat transcripts), where stripping "comments" would be wrong anyway.
    """
    try:
        return " ".join(t.value for t in tokenize(text) if t.kind != "eof")
    except LuauSyntaxError:
        return re.sub(r"\s+", " ", text).strip()


def quality_check(doc: Document, min_chars: int = 40, max_chars: int = 200_000) -> str | None:
    """Return a rejection reason, or None if the document is acceptable."""
    n = len(doc.text)
    if n < min_chars:
        return "too_short"
    if n > max_chars:
        return "too_long"
    alnum = sum(c.isalnum() for c in doc.text) / n
    if alnum < 0.25:
        return "low_alnum_ratio"
    if max((len(line) for line in doc.text.splitlines()), default=0) > 1000:
        return "minified_or_generated"
    if doc.kind == "luau":
        if not check_structure(doc.text).ok:
            return "luau_structure"
        if any(f.rule == "GM001" for f in lint(doc.text)):
            return "unsafe_loadstring"
    return None


class MinHasher:
    """MinHash over token 5-shingles for near-duplicate detection (Broder 1997).

    Uses the Mersenne prime 2^31 - 1 so (a*h + b) fits in uint64 and all
    permutations are computed in one vectorized numpy expression.
    """

    PRIME = (1 << 31) - 1

    def __init__(self, num_perm: int = 64, shingle: int = 5, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.a = rng.integers(1, self.PRIME, size=num_perm, dtype=np.uint64)
        self.b = rng.integers(0, self.PRIME, size=num_perm, dtype=np.uint64)
        self.shingle = shingle

    def signature(self, text: str) -> np.ndarray:
        words = re.findall(r"\w+|[^\w\s]", text)
        k = self.shingle
        grams = {" ".join(words[i : i + k]) for i in range(max(1, len(words) - k + 1))}
        hashes = np.fromiter(
            (int.from_bytes(hashlib.blake2b(g.encode(), digest_size=4).digest(), "little") & self.PRIME for g in grams),
            dtype=np.uint64, count=len(grams),
        )
        return ((self.a[:, None] * hashes[None, :] + self.b[:, None]) % np.uint64(self.PRIME)).min(axis=1)

    @staticmethod
    def similarity(s1: np.ndarray, s2: np.ndarray) -> float:
        return float((s1 == s2).mean())


class LSHIndex:
    """Banded LSH over MinHash signatures: near-linear candidate retrieval."""

    def __init__(self, bands: int = 16):
        self.bands = bands
        self.tables: list[dict[bytes, list[int]]] = [{} for _ in range(bands)]
        self.sigs: list[np.ndarray] = []

    def _keys(self, sig: np.ndarray):
        rows = len(sig) // self.bands
        return (sig[i * rows : (i + 1) * rows].tobytes() for i in range(self.bands))

    def query(self, sig: np.ndarray, threshold: float) -> int | None:
        seen: set[int] = set()
        for table, key in zip(self.tables, self._keys(sig)):
            for j in table.get(key, ()):
                if j not in seen:
                    seen.add(j)
                    if MinHasher.similarity(sig, self.sigs[j]) >= threshold:
                        return j
        return None

    def add(self, sig: np.ndarray) -> int:
        idx = len(self.sigs)
        self.sigs.append(sig)
        for table, key in zip(self.tables, self._keys(sig)):
            table.setdefault(key, []).append(idx)
        return idx


def build_corpus(docs: Iterable[Document], near_dup_threshold: float = 0.85) -> tuple[list[Document], FilterReport]:
    report = FilterReport()
    seen_exact: set[str] = set()
    hasher = MinHasher()
    index = LSHIndex()
    kept: list[Document] = []
    for doc in docs:
        reason = quality_check(doc)
        if reason:
            report.reject(reason)
            continue
        norm = normalize_for_dedup(doc.text)
        key = hashlib.sha256(norm.encode()).hexdigest()
        if key in seen_exact:
            report.reject("exact_duplicate")
            continue
        sig = hasher.signature(norm)
        if index.query(sig, near_dup_threshold) is not None:
            report.reject("near_duplicate")
            continue
        seen_exact.add(key)
        index.add(sig)
        doc.meta["minhash"] = sig  # reused by cluster-aware splitting
        kept.append(doc)
        report.accepted += 1
    return kept, report


def write_manifest(docs: list[Document], report: FilterReport, path: str | Path) -> None:
    entries = [{"sha": d.sha, "source": d.source, "license": d.license, "path": d.path, "kind": d.kind, "chars": len(d.text)} for d in docs]
    Path(path).write_text(json.dumps({"documents": entries, "report": asdict(report)}, indent=2))
