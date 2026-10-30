"""Pretraining loop: single process or multi-process data parallel (DDP).

Launch multi-GPU (or multi-process CPU for testing) with
`torchrun --nproc_per_node=N -m godmode train ...`. Each rank samples its own
batches (seed + rank); rank 0 evaluates, logs and checkpoints. FSDP, tensor,
pipeline and expert parallelism are designed in docs/ARCHITECTURE.md §6 and
not implemented yet.
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
    device: str = "auto"  # auto -> cuda if available else cpu
    precision: str = "auto"  # auto -> bf16 on cuda with bf16 support, else fp32
    grad_accum: int = 1
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


def _dist_info() -> tuple[int, int, int]:
    import os

    return int(os.environ.get("RANK", 0)), int(os.environ.get("WORLD_SIZE", 1)), int(os.environ.get("LOCAL_RANK", 0))


def resolve_device(cfg: TrainConfig, local_rank: int = 0) -> tuple[str, torch.dtype | None]:
    device = cfg.device
    if device == "auto":
        device = f"cuda:{local_rank}" if torch.cuda.is_available() else "cpu"
    precision = cfg.precision
    if precision == "auto":
        precision = "bf16" if device.startswith("cuda") and torch.cuda.is_bf16_supported() else "fp32"
    return device, (torch.bfloat16 if precision == "bf16" else None)


def train(model_cfg: ModelConfig, cfg: TrainConfig, tok: BPETokenizer, train_tokens: np.ndarray, val_tokens: np.ndarray, log=print,
          test_tokens: np.ndarray | None = None) -> dict:
    """Train, select the best checkpoint on val, and report test loss of that checkpoint if a test split is given."""
    import contextlib

    import torch.distributed as dist

    rank, world, local_rank = _dist_info()
    distributed = world > 1
    device, amp_dtype = resolve_device(cfg, local_rank)
    if distributed and not dist.is_initialized():
        dist.init_process_group("nccl" if device.startswith("cuda") else "gloo")
    if device.startswith("cuda"):
        torch.cuda.set_device(device)
    main = rank == 0
    set_seed(cfg.seed)  # identical init on every rank
    out = Path(cfg.out_dir)
    if main:
        out.mkdir(parents=True, exist_ok=True)
    raw = GodmodeLM(model_cfg).to(device)
    model = raw
    if distributed:
        # Experts that receive no tokens in a step have no gradient.
        model = torch.nn.parallel.DistributedDataParallel(raw, find_unused_parameters=model_cfg.n_experts > 0)
    opt = make_optimizer(raw, cfg)
    train_stream = TokenStream(train_tokens, cfg.seq_len, seed=cfg.seed + 1000 * rank)
    val_stream = TokenStream(val_tokens, cfg.seq_len, seed=cfg.seed + 1)

    def autocast():
        return torch.autocast(device.split(":")[0], dtype=amp_dtype) if amp_dtype else contextlib.nullcontext()

    log_file = (out / "log.jsonl").open("w") if main else None
    best_val, history = float("inf"), []
    expert_load = None
    t0 = time.time()
    for step in range(cfg.steps):
        for g in opt.param_groups:
            g["lr"] = lr_at(step, cfg)
        opt.zero_grad(set_to_none=True)
        lm_total = 0.0
        for micro in range(cfg.grad_accum):
            x, y = train_stream.batch(cfg.batch_size)
            sync = not distributed or micro == cfg.grad_accum - 1
            with (contextlib.nullcontext() if sync else model.no_sync()), autocast():
                res = model(x.to(device), y.to(device))
                (res.loss / cfg.grad_accum).backward()
            lm_total += res.lm_loss.item() / cfg.grad_accum
            if res.moe_stats:
                counts = torch.stack([s.tokens_per_expert for s in res.moe_stats]).sum(0)
                expert_load = counts if expert_load is None else expert_load + counts
        gnorm = torch.nn.utils.clip_grad_norm_(raw.parameters(), cfg.grad_clip).item()
        opt.step()

        if (step % cfg.eval_every == 0 or step == cfg.steps - 1) and main:
            with autocast():
                val = evaluate(raw, val_stream, cfg.eval_batches, cfg.batch_size, device)
            rec = {"step": step, "train_loss": round(lm_total, 4), "val_loss": round(val, 4),
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
                save_checkpoint(out / "best.pt", raw, tok, step, rec, cfg)
    if distributed:
        dist.barrier()
    if not main:
        return {"rank": rank}
    log_file.close()
    save_checkpoint(out / "last.pt", raw, tok, cfg.steps - 1, history[-1], cfg)
    extra = {}
    if test_tokens is not None and len(test_tokens) > cfg.seq_len + 1:
        best, _, _ = load_checkpoint(out / "best.pt", device)
        test_stream = TokenStream(test_tokens, cfg.seq_len, seed=cfg.seed + 2)
        with autocast():
            extra["test_loss"] = round(evaluate(best, test_stream, cfg.eval_batches, cfg.batch_size, device), 4)
        log(json.dumps({"test_loss_of_best_val_checkpoint": extra["test_loss"]}))
    return {
        **extra,
        "best_val_loss": round(best_val, 4),
        "final": history[-1],
        "params_total": raw.num_params(),
        "params_active": raw.num_params(active_only=True),
        "world_size": world,
        "device": device,
        "precision": "bf16" if amp_dtype else "fp32",
        "wall_time_s": round(time.time() - t0, 1),
        "history": history,
    }
