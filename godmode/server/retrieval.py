"""BM25 retrieval over the licensed Luau corpus (no third-party deps).

The index is built lazily on first use and cached as a pickle under
runs/retrieval/ keyed by the corpus directories' file sizes and mtimes. Results
carry source + license so replies can cite them. Retrieved text is presented as
"retrieved examples", never as model output.
"""

from __future__ import annotations

import hashlib
import math
import pickle
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEFAULT_DIRS = ("data/seed_corpus", "data/external/roblox_luau_corpus")
MAX_DOC_CHARS = 6000
CACHE_VERSION = 1
STOPWORDS = frozenset("""a an and are as at be by do does for from how i if in is it its local my of on or the then this
to use using what when where which why with you your end function return nil true false can should get""".split())


def tokenize(text: str) -> list[str]:
    """Identifier-aware tokens: splits camelCase / snake_case, keeps the whole identifier too."""
    out: list[str] = []
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        low = word.lower()
        parts = [p.lower() for p in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", word)]
        if len(parts) > 1 and low not in STOPWORDS and len(low) > 1:
            out.append(low)
        for p in parts or [low]:
            if len(p) > 1 and p not in STOPWORDS:
                out.append(p)
    return out


@dataclass
class Hit:
    score: float
    source: str
    license: str
    path: str
    snippet: str

    def to_json(self) -> dict:
        return {"score": round(self.score, 3), "source": self.source, "license": self.license, "path": self.path,
                "snippet": self.snippet}


class BM25Index:
    def __init__(self, docs: list[dict], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs  # {"text", "source", "license", "path"}
        postings: dict[str, tuple[list[int], list[int]]] = {}
        lengths = np.zeros(len(docs), dtype=np.float32)
        for i, d in enumerate(docs):
            toks = tokenize(d["text"])
            lengths[i] = len(toks)
            for term, tf in Counter(toks).items():
                ids, tfs = postings.setdefault(term, ([], []))
                ids.append(i)
                tfs.append(tf)
        self.lengths = lengths
        self.avgdl = float(lengths.mean()) if len(docs) else 1.0
        n = len(docs)
        self.postings = {t: (np.asarray(ids, dtype=np.int32), np.asarray(tfs, dtype=np.float32),
                             math.log(1 + (n - len(ids) + 0.5) / (len(ids) + 0.5)))
                         for t, (ids, tfs) in postings.items()}

    def __len__(self) -> int:
        return len(self.docs)

    def search(self, query: str, k: int = 3) -> list[tuple[int, float]]:
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms or not self.docs:
            return []
        scores = np.zeros(len(self.docs), dtype=np.float32)
        for t in terms:
            if t not in self.postings:
                continue
            ids, tfs, idf = self.postings[t]
            norm = self.k1 * (1 - self.b + self.b * self.lengths[ids] / self.avgdl)
            scores[ids] += idf * tfs * (self.k1 + 1) / (tfs + norm)
        k = min(k, len(self.docs))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(i), float(scores[i])) for i in top if scores[i] > 0]


def best_snippet(text: str, query: str, max_lines: int = 14) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.strip()
    q = set(tokenize(query))
    hits = [len(q.intersection(tokenize(ln))) for ln in lines]
    best, best_start = -1, 0
    window = sum(hits[:max_lines])
    for start in range(0, len(lines) - max_lines + 1):
        if start:
            window += hits[start + max_lines - 1] - hits[start - 1]
        if window > best:
            best, best_start = window, start
    return "\n".join(lines[best_start:best_start + max_lines]).strip()


class Retriever:
    """Lazily built, pickle-cached BM25 retriever over corpus directories."""

    def __init__(self, dirs=DEFAULT_DIRS, cache_dir: str | Path = "runs/retrieval", root: str | Path = "."):
        self.root = Path(root)
        self.dirs = [self.root / d for d in dirs]
        self.cache_dir = self.root / cache_dir if not Path(cache_dir).is_absolute() else Path(cache_dir)
        self._index: BM25Index | None = None
        self._lock = threading.Lock()
        self.detail = "index is built lazily on first question"

    def _fingerprint(self) -> str:
        h = hashlib.sha1(f"v{CACHE_VERSION}".encode())
        for d in self.dirs:
            h.update(str(d.resolve()).encode())
            if d.exists():
                for p in sorted(d.rglob("*")):
                    if p.is_file():
                        st = p.stat()
                        h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
        return h.hexdigest()[:16]

    def _load_docs(self) -> list[dict]:
        from godmode.data import load_source_dir

        docs: list[dict] = []
        for d in self.dirs:
            if not (d / "SOURCE.json").exists():
                continue
            try:
                loaded = load_source_dir(d)
            except (PermissionError, ValueError, OSError):
                continue
            for doc in loaded:
                docs.append({"text": doc.text[:MAX_DOC_CHARS], "source": doc.source, "license": doc.license,
                             "path": str(Path(doc.path).relative_to(self.root)) if Path(doc.path).is_relative_to(self.root)
                             else doc.path})
        return docs

    def index(self) -> BM25Index:
        with self._lock:
            if self._index is not None:
                return self._index
            fp = self._fingerprint()
            cache = self.cache_dir / f"bm25-{fp}.pkl"
            if cache.exists():
                try:
                    with cache.open("rb") as f:
                        self._index = pickle.load(f)
                    self.detail = f"loaded cached index {cache}"
                    return self._index
                except Exception:  # corrupt cache: rebuild
                    pass
            self._index = BM25Index(self._load_docs())
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(".tmp")
                with tmp.open("wb") as f:
                    pickle.dump(self._index, f, protocol=pickle.HIGHEST_PROTOCOL)
                tmp.replace(cache)
                self.detail = f"built index and cached it at {cache}"
            except OSError as e:
                self.detail = f"built index (cache not written: {e})"
            return self._index

    @property
    def ready(self) -> bool:
        return self._index is not None

    def status(self) -> dict:
        return {"ready": self.ready, "documents": len(self._index) if self._index else 0, "detail": self.detail}

    def search(self, query: str, k: int = 3) -> list[Hit]:
        idx = self.index()
        return [Hit(score, idx.docs[i]["source"], idx.docs[i]["license"], idx.docs[i]["path"],
                    best_snippet(idx.docs[i]["text"], query)) for i, score in idx.search(query, k)]
