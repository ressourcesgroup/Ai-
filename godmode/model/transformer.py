"""GODMODE core network: a decoder-only transformer built from first principles.

Components (references in docs/ARCHITECTURE.md §3.2):
- RMSNorm (Zhang & Sennrich 2019), pre-norm residual blocks.
- Rotary position embeddings (Su et al. 2021).
- Grouped-query attention (Ainslie et al. 2023) with a KV cache for inference.
- SwiGLU feed-forward (Shazeer 2020).
- Optional top-k sparse MoE layers with Switch-style load-balancing loss
  (Fedus et al. 2021) and router z-loss (Zoph et al. 2022).
- Optional recurrent depth: the block stack is re-applied n_loops times with
  shared weights (Dehghani et al. 2019, "Universal Transformers").
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from .config import ModelConfig

KVCache = list[tuple[torch.Tensor, torch.Tensor] | None]


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.weight * x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)


def rope_tables(head_dim: int, max_len: int, theta: float) -> tuple[torch.Tensor, torch.Tensor]:
    inv = 1.0 / (theta ** (torch.arange(0, head_dim, 2).float() / head_dim))
    freqs = torch.outer(torch.arange(max_len).float(), inv)
    return freqs.cos(), freqs.sin()


def apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    # x: (B, H, T, D); cos/sin: (T, D/2)
    x1, x2 = x[..., 0::2], x[..., 1::2]
    out = torch.stack((x1 * cos - x2 * sin, x1 * sin + x2 * cos), dim=-1)
    return out.flatten(-2)


class Attention(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.n_heads, self.n_kv, self.hd = cfg.n_heads, cfg.n_kv_heads, cfg.head_dim
        self.q = nn.Linear(cfg.d_model, cfg.n_heads * self.hd, bias=False)
        self.kv = nn.Linear(cfg.d_model, 2 * cfg.n_kv_heads * self.hd, bias=False)
        self.o = nn.Linear(cfg.n_heads * self.hd, cfg.d_model, bias=False)
        self.dropout = cfg.dropout

    def forward(self, x, cos, sin, past=None):
        B, T, _ = x.shape
        q = self.q(x).view(B, T, self.n_heads, self.hd).transpose(1, 2)
        k, v = self.kv(x).view(B, T, 2, self.n_kv, self.hd).permute(2, 0, 3, 1, 4)
        q, k = apply_rope(q, cos, sin), apply_rope(k, cos, sin)
        if past is not None:
            k = torch.cat([past[0], k], dim=2)
            v = torch.cat([past[1], v], dim=2)
        present = (k, v)
        rep = self.n_heads // self.n_kv
        if rep > 1:
            k, v = k.repeat_interleave(rep, dim=1), v.repeat_interleave(rep, dim=1)
        # With a cache, the T new queries attend to all S keys; causal mask is
        # offset so query i sees keys [0, S - T + i].
        S = k.shape[2]
        if past is None:
            y = F.scaled_dot_product_attention(q, k, v, is_causal=True, dropout_p=self.dropout if self.training else 0.0)
        else:
            mask = torch.ones(T, S, dtype=torch.bool, device=x.device).tril(diagonal=S - T)
            y = F.scaled_dot_product_attention(q, k, v, attn_mask=mask)
        return self.o(y.transpose(1, 2).reshape(B, T, -1)), present


class SwiGLU(nn.Module):
    def __init__(self, d_model: int, d_ff: int):
        super().__init__()
        self.gate_up = nn.Linear(d_model, 2 * d_ff, bias=False)
        self.down = nn.Linear(d_ff, d_model, bias=False)

    def forward(self, x):
        g, u = self.gate_up(x).chunk(2, dim=-1)
        return self.down(F.silu(g) * u)


@dataclass
class MoEStats:
    aux_loss: torch.Tensor
    z_loss: torch.Tensor
    tokens_per_expert: torch.Tensor  # (E,) float counts of top-k assignments


class MoE(nn.Module):
    """Top-k routed mixture of SwiGLU experts.

    Dispatch is a straightforward per-expert gather/scatter; it is correct and
    simple, not FLOP-optimal. Expert parallelism is a Phase 9 concern.
    """

    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.n_experts, self.top_k = cfg.n_experts, cfg.top_k
        self.router = nn.Linear(cfg.d_model, cfg.n_experts, bias=False)
        self.experts = nn.ModuleList(SwiGLU(cfg.d_model, cfg.expert_d_ff) for _ in range(cfg.n_experts))

    def forward(self, x):
        B, T, D = x.shape
        flat = x.reshape(-1, D)
        logits = self.router(flat)  # (N, E)
        probs = logits.float().softmax(-1)
        topv, topi = probs.topk(self.top_k, dim=-1)
        topv = (topv / topv.sum(-1, keepdim=True)).to(x.dtype)

        out = torch.zeros_like(flat)
        for e, expert in enumerate(self.experts):
            token_idx, slot = (topi == e).nonzero(as_tuple=True)
            if token_idx.numel() == 0:
                continue
            out.index_add_(0, token_idx, expert(flat[token_idx]) * topv[token_idx, slot, None])

        counts = F.one_hot(topi, self.n_experts).sum(dim=(0, 1)).float()  # (E,)
        frac_tokens = counts / counts.sum()
        frac_probs = probs.mean(0)
        aux = self.n_experts * (frac_tokens * frac_probs).sum()
        z = torch.logsumexp(logits.float(), dim=-1).pow(2).mean()
        return out.view(B, T, D), MoEStats(aux, z, counts.detach())


class Block(nn.Module):
    def __init__(self, cfg: ModelConfig, layer_idx: int):
        super().__init__()
        self.norm1, self.norm2 = RMSNorm(cfg.d_model), RMSNorm(cfg.d_model)
        self.attn = Attention(cfg)
        self.is_moe = cfg.is_moe_layer(layer_idx)
        self.mlp = MoE(cfg) if self.is_moe else SwiGLU(cfg.d_model, cfg.d_ff)
        self.drop = nn.Dropout(cfg.dropout)

    def forward(self, x, cos, sin, past=None):
        a, present = self.attn(self.norm1(x), cos, sin, past)
        x = x + self.drop(a)
        stats = None
        if self.is_moe:
            m, stats = self.mlp(self.norm2(x))
        else:
            m = self.mlp(self.norm2(x))
        return x + self.drop(m), present, stats


@dataclass
class ModelOutput:
    logits: torch.Tensor
    loss: torch.Tensor | None
    lm_loss: torch.Tensor | None
    moe_stats: list[MoEStats]
    cache: KVCache | None


class GodmodeLM(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.cfg = cfg
        self.embed = nn.Embedding(cfg.vocab_size, cfg.d_model)
        self.blocks = nn.ModuleList(Block(cfg, i) for i in range(cfg.n_layers))
        self.norm = RMSNorm(cfg.d_model)
        self.head = nn.Linear(cfg.d_model, cfg.vocab_size, bias=False)
        if cfg.tie_embeddings:
            self.head.weight = self.embed.weight
        cos, sin = rope_tables(cfg.head_dim, cfg.max_seq_len, cfg.rope_theta)
        self.register_buffer("rope_cos", cos, persistent=False)
        self.register_buffer("rope_sin", sin, persistent=False)
        self.apply(self._init)
        # Scaled init on residual projections (GPT-2 style) for depth stability.
        depth = cfg.n_layers * cfg.n_loops
        for name, p in self.named_parameters():
            if name.endswith(("o.weight", "down.weight")):
                nn.init.normal_(p, std=0.02 / math.sqrt(2 * depth))

    @staticmethod
    def _init(m):
        if isinstance(m, nn.Linear):
            nn.init.normal_(m.weight, std=0.02)
        elif isinstance(m, nn.Embedding):
            nn.init.normal_(m.weight, std=0.02)

    def num_params(self, active_only: bool = False) -> int:
        total = sum(p.numel() for p in self.parameters())
        if not active_only or not self.cfg.n_experts:
            return total
        inactive = 0
        for b in self.blocks:
            if b.is_moe:
                per_expert = sum(p.numel() for p in b.mlp.experts[0].parameters())
                inactive += per_expert * (self.cfg.n_experts - self.cfg.top_k)
        return total - inactive

    def forward(self, idx: torch.Tensor, targets: torch.Tensor | None = None, cache: KVCache | None = None, use_cache: bool = False) -> ModelOutput:
        B, T = idx.shape
        offset = 0
        if cache is not None and cache[0] is not None:
            offset = cache[0][0].shape[2]
        if offset + T > self.cfg.max_seq_len:
            raise ValueError(f"sequence length {offset + T} exceeds max_seq_len {self.cfg.max_seq_len}")
        cos, sin = self.rope_cos[offset : offset + T], self.rope_sin[offset : offset + T]

        x = self.embed(idx)
        n_slots = self.cfg.n_layers * self.cfg.n_loops
        new_cache: KVCache = [None] * n_slots if (use_cache or cache is not None) else None
        stats: list[MoEStats] = []
        slot = 0
        for _ in range(self.cfg.n_loops):
            for block in self.blocks:
                past = cache[slot] if cache is not None else None
                x, present, s = block(x, cos, sin, past)
                if new_cache is not None:
                    new_cache[slot] = present
                if s is not None:
                    stats.append(s)
                slot += 1
        logits = self.head(self.norm(x))

        loss = lm_loss = None
        if targets is not None:
            lm_loss = F.cross_entropy(logits.float().reshape(-1, logits.size(-1)), targets.reshape(-1), ignore_index=-100)
            loss = lm_loss
            if stats:
                aux = torch.stack([s.aux_loss for s in stats]).mean()
                z = torch.stack([s.z_loss for s in stats]).mean()
                loss = loss + self.cfg.aux_loss_coef * aux + self.cfg.z_loss_coef * z
        return ModelOutput(logits, loss, lm_loss, stats, new_cache)

    @torch.no_grad()
    def generate(self, idx: torch.Tensor, max_new_tokens: int, temperature: float = 1.0, top_k: int | None = None,
                 stop_ids: set[int] | None = None, generator: torch.Generator | None = None) -> torch.Tensor:
        """Autoregressive sampling with a KV cache. temperature == 0 means greedy.

        Prompts longer than the context keep their last tokens. With a batch,
        each row stops at its first stop id (later positions repeat it) and the
        loop ends when every row has stopped. Restores the previous train/eval mode.
        """
        was_training = self.training
        self.eval()
        idx = idx[:, -(self.cfg.max_seq_len - 1):]
        max_new_tokens = min(max_new_tokens, self.cfg.max_seq_len - idx.shape[1])
        stop = torch.tensor(sorted(stop_ids or ()), dtype=idx.dtype, device=idx.device)
        done = torch.zeros(idx.shape[0], dtype=torch.bool, device=idx.device)
        try:
            out = self(idx, use_cache=True)
            cache, logits = out.cache, out.logits[:, -1]
            for step in range(max_new_tokens):
                if temperature == 0:
                    nxt = logits.argmax(-1, keepdim=True)
                else:
                    logits = logits / temperature
                    if top_k:
                        kth = logits.topk(min(top_k, logits.size(-1))).values[:, -1, None]
                        logits = logits.masked_fill(logits < kth, float("-inf"))
                    nxt = torch.multinomial(logits.softmax(-1), 1, generator=generator)
                if stop.numel():
                    nxt = torch.where(done[:, None], idx[:, -1:], nxt)
                    done |= torch.isin(nxt[:, 0], stop)
                idx = torch.cat([idx, nxt], dim=1)
                if (stop.numel() and bool(done.all())) or step == max_new_tokens - 1:
                    break
                out = self(nxt, cache=cache)
                cache, logits = out.cache, out.logits[:, -1]
        finally:
            self.train(was_training)
        return idx
