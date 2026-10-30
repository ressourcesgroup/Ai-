from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ModelConfig:
    vocab_size: int = 4096
    d_model: int = 256
    n_layers: int = 4
    n_heads: int = 8
    n_kv_heads: int = 4  # grouped-query attention; == n_heads means plain MHA
    d_ff: int = 0  # 0 -> round(8/3 * d_model) to a multiple of 64 (SwiGLU convention)
    max_seq_len: int = 512
    dropout: float = 0.0
    rope_theta: float = 10000.0
    tie_embeddings: bool = True

    # Sparse Mixture-of-Experts. n_experts == 0 means a dense model.
    n_experts: int = 0
    top_k: int = 2
    moe_every: int = 2  # every Nth block (1-indexed) uses MoE instead of a dense MLP
    expert_d_ff: int = 0  # 0 -> d_ff // top_k so active FLOPs roughly match dense
    aux_loss_coef: float = 0.01
    z_loss_coef: float = 1e-3
    expert_names: list[str] = field(default_factory=list)

    # Recurrent depth: run the block stack n_loops times with shared weights.
    n_loops: int = 1

    def __post_init__(self) -> None:
        if self.d_ff == 0:
            self.d_ff = int(round(8 / 3 * self.d_model / 64)) * 64
        if self.n_experts and self.expert_d_ff == 0:
            self.expert_d_ff = max(64, self.d_ff // self.top_k)
        if self.d_model % self.n_heads:
            raise ValueError("d_model must be divisible by n_heads")
        if self.n_heads % self.n_kv_heads:
            raise ValueError("n_heads must be divisible by n_kv_heads")
        if self.n_experts and not 1 <= self.top_k <= self.n_experts:
            raise ValueError("top_k must be in [1, n_experts]")
        if self.n_loops < 1:
            raise ValueError("n_loops must be >= 1")

    @property
    def head_dim(self) -> int:
        return self.d_model // self.n_heads

    def is_moe_layer(self, layer_idx: int) -> bool:
        return self.n_experts > 0 and (layer_idx + 1) % self.moe_every == 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ModelConfig":
        return cls(**d)

    @classmethod
    def from_json(cls, path: str | Path) -> "ModelConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))
