"""Token packing and batching with a leakage-free, document-level split."""

from __future__ import annotations

import hashlib

import numpy as np
import torch

from ..tokenizer import BPETokenizer
from .corpus import Document, MinHasher, normalize_for_dedup


def split_documents(docs: list[Document], val_fraction: float = 0.1) -> tuple[list[Document], list[Document]]:
    """Deterministic split by content hash, so a document never lands in both sets."""
    train, val = [], []
    for d in docs:
        bucket = int(hashlib.sha256(d.text.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        (val if bucket < val_fraction else train).append(d)
    if not val and len(train) > 1:
        val.append(train.pop())
    return train, val


def _bucket(key: str) -> float:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def cluster_documents(docs: list[Document], threshold: float = 0.5, bands: int = 16) -> list[int]:
    """Group documents whose MinHash Jaccard estimate is >= threshold (union-find).

    Candidate pairs come from LSH banding, so cost stays near-linear in the
    number of documents; candidates are confirmed against the full signature.
    """
    hasher = MinHasher()
    sigs = [d.meta.get("minhash") if d.meta.get("minhash") is not None else hasher.signature(normalize_for_dedup(d.text)) for d in docs]
    parent = list(range(len(docs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    rows = len(sigs[0]) // bands if sigs else 0
    for b in range(bands):
        buckets: dict[bytes, list[int]] = {}
        for i, sig in enumerate(sigs):
            buckets.setdefault(sig[b * rows : (b + 1) * rows].tobytes(), []).append(i)
        for members in buckets.values():
            for j in members[1:]:
                ri, rj = find(members[0]), find(j)
                if ri != rj and MinHasher.similarity(sigs[members[0]], sigs[j]) >= threshold:
                    parent[rj] = ri
    return [find(i) for i in range(len(docs))]


def split_clustered(docs: list[Document], val_fraction: float = 0.1, test_fraction: float = 0.1,
                    threshold: float = 0.5) -> tuple[list[Document], list[Document], list[Document]]:
    """Train/val/test split where near-duplicate clusters never straddle splits.

    A plain per-document split leaks when the corpus contains templated
    variants (measured: 87% of val 13-grams appeared in train). Whole clusters
    are assigned by a hash of their first member, so the split is deterministic.
    """
    clusters = cluster_documents(docs, threshold)
    first: dict[int, str] = {}
    for d, c in zip(docs, clusters):
        first.setdefault(c, d.sha)
    train, val, test = [], [], []
    for d, c in zip(docs, clusters):
        u = _bucket(first[c])
        (test if u < test_fraction else val if u < test_fraction + val_fraction else train).append(d)
    return train, val, test


def ngram_overlap(held_out: list[str], train: list[str], n: int = 13) -> float:
    """Fraction of held-out n-grams (over normalized tokens) that also occur in train."""
    def grams(text: str) -> set[str]:
        words = normalize_for_dedup(text).split()
        return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}

    train_grams: set[str] = set().union(*(grams(t) for t in train)) if train else set()
    held = [grams(t) for t in held_out]
    total = sum(len(g) for g in held)
    return round(sum(len(g & train_grams) for g in held) / total, 4) if total else 0.0


def pack(docs: list[Document], tok: BPETokenizer) -> np.ndarray:
    eos = tok.token_id("<|eos|>")
    ids: list[int] = []
    for d in docs:
        ids.extend(tok.encode(d.text, allow_special=(d.kind == "chat")))
        ids.append(eos)
    dtype = np.uint16 if tok.vocab_size < 65536 else np.uint32
    return np.array(ids, dtype=dtype)


class TokenStream:
    """Random fixed-length windows over a packed token array."""

    def __init__(self, tokens: np.ndarray, seq_len: int, seed: int = 0):
        if len(tokens) <= seq_len + 1:
            raise ValueError(f"need more than {seq_len + 1} tokens, got {len(tokens)}")
        self.tokens = torch.from_numpy(tokens.astype(np.int64))
        self.seq_len = seq_len
        self.gen = torch.Generator().manual_seed(seed)

    def batch(self, batch_size: int) -> tuple[torch.Tensor, torch.Tensor]:
        starts = torch.randint(0, len(self.tokens) - self.seq_len - 1, (batch_size,), generator=self.gen)
        x = torch.stack([self.tokens[s : s + self.seq_len] for s in starts])
        y = torch.stack([self.tokens[s + 1 : s + 1 + self.seq_len] for s in starts])
        return x, y

    def sequential_batches(self, batch_size: int):
        """Non-overlapping windows covering the stream once (for evaluation)."""
        n = (len(self.tokens) - 1) // self.seq_len
        for i in range(0, n, batch_size):
            idx = range(i, min(i + batch_size, n))
            x = torch.stack([self.tokens[j * self.seq_len : (j + 1) * self.seq_len] for j in idx])
            y = torch.stack([self.tokens[j * self.seq_len + 1 : (j + 1) * self.seq_len + 1] for j in idx])
            yield x, y
