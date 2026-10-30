"""Token packing and batching with a leakage-free, document-level split."""

from __future__ import annotations

import hashlib

import numpy as np
import torch

from ..tokenizer import BPETokenizer
from .corpus import Document


def split_documents(docs: list[Document], val_fraction: float = 0.1) -> tuple[list[Document], list[Document]]:
    """Deterministic split by content hash, so a document never lands in both sets."""
    train, val = [], []
    for d in docs:
        bucket = int(hashlib.sha256(d.text.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        (val if bucket < val_fraction else train).append(d)
    if not val and len(train) > 1:
        val.append(train.pop())
    return train, val


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
