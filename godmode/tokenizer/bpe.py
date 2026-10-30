"""Byte-level BPE tokenizer trained from scratch (no external vocabularies).

Design choices (see docs/ARCHITECTURE.md §3.1):
- Byte-level base alphabet: any UTF-8 text round-trips losslessly.
- Code-aware pre-tokenization: newline+indentation is its own chunk so the
  model sees indentation structure; identifiers keep one leading space;
  punctuation runs (`..=`, `~=`, `::`) can merge into single tokens.
- Special tokens for document boundaries, fill-in-the-middle and chat roles.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

SPECIAL_TOKENS = (
    "<|bos|>", "<|eos|>", "<|pad|>",
    "<|fim_prefix|>", "<|fim_middle|>", "<|fim_suffix|>",
    "<|system|>", "<|user|>", "<|assistant|>",
)

PRETOKENIZE = re.compile(
    r"\n[ \t]*"                      # newline + indentation
    r"| ?[A-Za-z_][A-Za-z0-9_]*"     # identifiers / keywords
    r"| ?[0-9]{1,3}"                 # digits in groups of <=3
    r"| ?[^\sA-Za-z0-9_]+"           # punctuation / operators / non-ASCII
    r"|[ \t]+(?![^\s])"              # trailing whitespace
    r"|[ \t]+"
    r"|\s"
)


def _merge(ids: tuple[int, ...], pair: tuple[int, int], new_id: int) -> tuple[int, ...]:
    out: list[int] = []
    i = 0
    while i < len(ids):
        if i + 1 < len(ids) and ids[i] == pair[0] and ids[i + 1] == pair[1]:
            out.append(new_id)
            i += 2
        else:
            out.append(ids[i])
            i += 1
    return tuple(out)


class BPETokenizer:
    def __init__(self, merges: list[tuple[int, int]] | None = None, specials: Iterable[str] = SPECIAL_TOKENS):
        self.specials = list(specials)
        self.special_to_id = {s: 256 + i for i, s in enumerate(self.specials)}
        self.merges: list[tuple[int, int]] = []
        self.ranks: dict[tuple[int, int], int] = {}
        self.vocab: dict[int, bytes] = {i: bytes([i]) for i in range(256)}
        for s, i in self.special_to_id.items():
            self.vocab[i] = s.encode()
        self._cache: dict[str, tuple[int, ...]] = {}
        for pair in merges or []:
            self._add_merge(pair)
        self._special_re = re.compile("(" + "|".join(re.escape(s) for s in self.specials) + ")")

    # ---- construction -------------------------------------------------
    @property
    def first_merge_id(self) -> int:
        return 256 + len(self.specials)

    @property
    def vocab_size(self) -> int:
        return self.first_merge_id + len(self.merges)

    def _add_merge(self, pair: tuple[int, int]) -> int:
        new_id = self.first_merge_id + len(self.merges)
        self.merges.append(pair)
        self.ranks[pair] = len(self.merges) - 1
        self.vocab[new_id] = self.vocab[pair[0]] + self.vocab[pair[1]]
        return new_id

    @classmethod
    def train(cls, texts: Iterable[str], vocab_size: int, min_frequency: int = 2) -> "BPETokenizer":
        tok = cls()
        if vocab_size < tok.first_merge_id:
            raise ValueError(f"vocab_size must be >= {tok.first_merge_id}")

        chunk_counts: Counter[str] = Counter()
        for text in texts:
            chunk_counts.update(PRETOKENIZE.findall(text))
        words = [tuple(c.encode()) for c in chunk_counts]
        freqs = list(chunk_counts.values())

        # Incremental pair statistics: pair -> count, pair -> words containing it.
        pair_counts: Counter[tuple[int, int]] = Counter()
        where: dict[tuple[int, int], set[int]] = defaultdict(set)
        for wi, w in enumerate(words):
            for p in zip(w, w[1:]):
                pair_counts[p] += freqs[wi]
                where[p].add(wi)

        while tok.vocab_size < vocab_size and pair_counts:
            # Tie-break deterministically on the pair value.
            pair, count = max(pair_counts.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
            if count < min_frequency:
                break
            new_id = tok._add_merge(pair)
            for wi in list(where.get(pair, ())):
                old = words[wi]
                new = _merge(old, pair, new_id)
                if new == old:
                    continue
                f = freqs[wi]
                for p in zip(old, old[1:]):
                    pair_counts[p] -= f
                    if pair_counts[p] <= 0:
                        del pair_counts[p]
                for p in zip(new, new[1:]):
                    pair_counts[p] += f
                    where[p].add(wi)
                words[wi] = new
            where.pop(pair, None)
            pair_counts.pop(pair, None)
        return tok

    # ---- encode / decode ---------------------------------------------
    def _encode_chunk(self, chunk: str) -> tuple[int, ...]:
        cached = self._cache.get(chunk)
        if cached is not None:
            return cached
        ids = tuple(chunk.encode())
        while len(ids) > 1:
            best = min(zip(ids, ids[1:]), key=lambda p: self.ranks.get(p, 1 << 60))
            rank = self.ranks.get(best)
            if rank is None:
                break
            ids = _merge(ids, best, self.first_merge_id + rank)
        if len(self._cache) < 200_000:
            self._cache[chunk] = ids
        return ids

    def encode(self, text: str, allow_special: bool = False) -> list[int]:
        out: list[int] = []
        parts = self._special_re.split(text) if allow_special else [text]
        for part in parts:
            if allow_special and part in self.special_to_id:
                out.append(self.special_to_id[part])
                continue
            for chunk in PRETOKENIZE.findall(part):
                out.extend(self._encode_chunk(chunk))
        return out

    def decode(self, ids: Iterable[int]) -> str:
        return b"".join(self.vocab[i] for i in ids).decode("utf-8", errors="replace")

    def token_id(self, special: str) -> int:
        return self.special_to_id[special]

    # ---- persistence ---------------------------------------------------
    def to_dict(self) -> dict:
        return {"format": "godmode-bpe-v1", "specials": self.specials, "merges": [list(m) for m in self.merges]}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: str | Path) -> "BPETokenizer":
        d = json.loads(Path(path).read_text())
        if d.get("format") != "godmode-bpe-v1":
            raise ValueError(f"unknown tokenizer format: {d.get('format')}")
        return cls([tuple(m) for m in d["merges"]], d["specials"])
