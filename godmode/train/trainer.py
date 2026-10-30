"""Single-process pretraining loop with checkpointing and reproducible seeding.

Distributed training (FSDP / tensor / pipeline / expert parallelism) is
designed in docs/ARCHITECTURE.md §6 and is NOT implemented here yet.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch

from ..data.dataset import TokenStream
from ..model import GodmodeLM, ModelConfig
from ..tokenizer import BPETokenizer


@dataclass
class TrainConfig:
    steps: int = 500
    batch_size: int = 16
    seq_len: int = 128
    lr: float = 3e-3
    min_lr: float = 3e-4
    warmup: int = 50
    weight_decay: float = 0.1
    grad_clip: float = 1.0
    eval_every: int = 100
    eval_batches: int = 20
    seed: int = 0
    device: str = "cpu"
    out_dir: str = "runs/default"


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def lr_at(step: int, cfg: TrainConfig) -> float:
    if step < cfg.warmup:
        return cfg.lr * (step + 1) / cfg.warmup
    progress = min(1.0, (step - cfg.warmup) / max(1, cfg.steps - cfg.warmup))
    return cfg.min_lr + 0.5 * (cfg.lr - cfg.min_lr) * (1 + math.cos(math.pi * progress))


def make_optimizer(model: GodmodeLM, cfg: TrainConfig) -> torch.optim.Optimizer:
    decay, no_decay = [], []
    for p in model.parameters():
        if not p.requires_grad:
            continue
        (decay if p.dim() >= 2 else no_decay).append(p)
    return torch.optim.AdamW(
        [{"params": decay, "weight_decay": cfg.weight_decay}, {"params": no_decay, "weight_decay": 0.0}],
        lr=cfg.lr, betas=(0.9, 0.95),
    )


@torch.no_grad()
def evaluate(model: GodmodeLM, stream: TokenStream, max_batches: int, batch_size: int, device: str) -> float:
    model.eval()
    total, count = 0.0, 0
    for i, (x, y) in enumerate(stream.sequential_batches(batch_size)):
        if i >= max_batches:
            break
        out = model(x.to(device), y.to(device))
        total += out.lm_loss.item() * x.numel()
        count += x.numel()
    model.train()
    return total / max(1, count)


def save_checkpoint(path: Path, model: GodmodeLM, tok: BPETokenizer, step: int, metrics: dict, train_cfg: TrainConfig) -> None:
    torch.save(
        {
            "format": "godmode-ckpt-v1",
            "model_config": model.cfg.to_dict(),
            "train_config": asdict(train_cfg),
            "tokenizer": tok.to_dict(),
            "tokenizer_fingerprint": tok.fingerprint(),
            "state_dict": model.state_dict(),
            "step": step,
            "metrics": metrics,
        },
        path,
    )


def load_checkpoint(path: str | Path, device: str = "cpu") -> tuple[GodmodeLM, BPETokenizer, dict]:
    ckpt = torch.load(path, map_location=device, weights_only=False)
    if ckpt.get("format") != "godmode-ckpt-v1":
        raise ValueError(f"unknown checkpoint format: {ckpt.get('format')}")
    tok = BPETokenizer([tuple(m) for m in ckpt["tokenizer"]["merges"]], ckpt["tokenizer"]["specials"])
    if tok.fingerprint() != ckpt["tokenizer_fingerprint"]:
        raise ValueError("tokenizer fingerprint mismatch: checkpoint is corrupt or was edited")
    model = GodmodeLM(ModelConfig.from_dict(ckpt["model_config"])).to(device)
    model.load_state_dict(ckpt["state_dict"])
    return model, tok, ckpt


def train(model_cfg: ModelConfig, cfg: TrainConfig, tok: BPETokenizer, train_tokens: np.ndarray, val_tokens: np.ndarray, log=print) -> dict:
    set_seed(cfg.seed)
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model = GodmodeLM(model_cfg).to(cfg.device)
    opt = make_optimizer(model, cfg)
    train_stream = TokenStream(train_tokens, cfg.seq_len, seed=cfg.seed)
    val_stream = TokenStream(val_tokens, cfg.seq_len, seed=cfg.seed + 1)

    log_path = out / "log.jsonl"
    log_file = log_path.open("w")
    best_val, history = float("inf"), []
    expert_load = None
    t0 = time.time()
    for step in range(cfg.steps):
        for g in opt.param_groups:
            g["lr"] = lr_at(step, cfg)
        x, y = train_stream.batch(cfg.batch_size)
        res = model(x.to(cfg.device), y.to(cfg.device))
        opt.zero_grad(set_to_none=True)
        res.loss.backward()
        gnorm = torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip).item()
        opt.step()
        if res.moe_stats:
            counts = torch.stack([s.tokens_per_expert for s in res.moe_stats]).sum(0)
            expert_load = counts if expert_load is None else expert_load + counts

        if step % cfg.eval_every == 0 or step == cfg.steps - 1:
            val = evaluate(model, val_stream, cfg.eval_batches, cfg.batch_size, cfg.device)
            rec = {"step": step, "train_loss": round(res.lm_loss.item(), 4), "val_loss": round(val, 4),
                   "lr": round(lr_at(step, cfg), 6), "grad_norm": round(gnorm, 3), "elapsed_s": round(time.time() - t0, 1)}
            if expert_load is not None:
                frac = (expert_load / expert_load.sum()).tolist()
                rec["expert_load"] = [round(f, 3) for f in frac]
                expert_load = None
            history.append(rec)
            log_file.write(json.dumps(rec) + "\n")
            log_file.flush()
            log(json.dumps(rec))
            if val < best_val:
                best_val = val
                save_checkpoint(out / "best.pt", model, tok, step, rec, cfg)
    log_file.close()
    save_checkpoint(out / "last.pt", model, tok, cfg.steps - 1, history[-1], cfg)
    return {
        "best_val_loss": round(best_val, 4),
        "final": history[-1],
        "params_total": model.num_params(),
        "params_active": model.num_params(active_only=True),
        "wall_time_s": round(time.time() - t0, 1),
        "history": history,
    }
