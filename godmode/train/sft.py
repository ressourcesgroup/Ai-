"""Supervised fine-tuning: prompt -> complete script, loss on the assistant tokens only.

Each example is ``<|bos|><|user|>{prompt}\\n<|assistant|>{script}<|eos|>``. Targets for
everything up to and including ``<|assistant|>`` are ``-100`` (ignored), so the model is
trained to *write* scripts, not to predict user prompts. Batches are padded per batch
(length-sorted buckets) instead of packed, so every example starts at position 0 —
exactly the situation at inference time.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import torch

from ..model import GodmodeLM, ModelConfig
from ..tokenizer import BPETokenizer
from .trainer import TrainConfig, load_checkpoint, make_optimizer, save_checkpoint, set_seed


@dataclass
class SFTConfig:
    init_from: str = "runs/small-full2/best.pt"
    out_dir: str = "runs/sft-scripts"
    max_seq_len: int = 768
    steps: int = 1500
    batch_size: int = 16
    lr: float = 6e-4
    min_lr: float = 6e-5
    warmup: int = 50
    weight_decay: float = 0.05
    grad_clip: float = 1.0
    eval_every: int = 100
    seed: int = 0
    threads: int = 0  # 0 -> torch default


def encode_example(tok: BPETokenizer, prompt: str, solution: str, max_len: int) -> tuple[list[int], list[int]] | None:
    head = tok.encode(f"<|bos|><|user|>{prompt}\n<|assistant|>", allow_special=True)
    body = tok.encode(solution) + [tok.token_id("<|eos|>")]
    ids = head + body
    if len(ids) > max_len:
        return None
    # targets[i] = ids[i+1]; mask predictions of prompt tokens.
    targets = ids[1:] + [-100]
    for i in range(len(head) - 1):
        targets[i] = -100
    return ids, targets


def make_batches(examples: list[tuple[list[int], list[int]]], batch_size: int, rng: random.Random, pad_id: int):
    """Length-bucketed shuffled batches, padded to the longest row (pads are ignored in the loss)."""
    order = sorted(range(len(examples)), key=lambda i: len(examples[i][0]) + rng.random())
    chunks = [order[i:i + batch_size] for i in range(0, len(order), batch_size)]
    rng.shuffle(chunks)
    for chunk in chunks:
        T = max(len(examples[i][0]) for i in chunk)
        x = torch.full((len(chunk), T), pad_id, dtype=torch.long)
        y = torch.full((len(chunk), T), -100, dtype=torch.long)
        for r, i in enumerate(chunk):
            ids, tg = examples[i]
            x[r, :len(ids)] = torch.tensor(ids)
            y[r, :len(tg)] = torch.tensor(tg)
        yield x, y


def lr_at(step: int, cfg: SFTConfig) -> float:
    if step < cfg.warmup:
        return cfg.lr * (step + 1) / cfg.warmup
    p = min(1.0, (step - cfg.warmup) / max(1, cfg.steps - cfg.warmup))
    return cfg.min_lr + 0.5 * (cfg.lr - cfg.min_lr) * (1 + math.cos(math.pi * p))


@torch.no_grad()
def masked_loss(model: GodmodeLM, examples, batch_size: int, pad_id: int) -> float:
    model.eval()
    total, count = 0.0, 0
    for x, y in make_batches(examples, batch_size, random.Random(0), pad_id):
        n = int((y != -100).sum())
        total += model(x, y).lm_loss.item() * n
        count += n
    model.train()
    return total / max(1, count)


def load_for_sft(path: str, max_seq_len: int) -> tuple[GodmodeLM, BPETokenizer]:
    """Load a checkpoint and (re)build it with a longer context; RoPE tables are not saved, so this is exact."""
    _, tok, ckpt = load_checkpoint(path)
    mc = ModelConfig.from_dict(ckpt["model_config"])
    mc.max_seq_len = max(mc.max_seq_len, max_seq_len)
    model = GodmodeLM(mc)
    model.load_state_dict(ckpt["state_dict"])
    return model, tok


def run_sft(cfg: SFTConfig, train_pairs: list[tuple[str, str]], val_pairs: list[tuple[str, str]],
            heldout_pairs: list[tuple[str, str]] | None = None, log=print) -> dict:
    set_seed(cfg.seed)
    if cfg.threads:
        torch.set_num_threads(cfg.threads)
    model, tok = load_for_sft(cfg.init_from, cfg.max_seq_len)
    pad_id = tok.token_id("<|pad|>")

    def enc(pairs):
        out = [encode_example(tok, p, s, cfg.max_seq_len) for p, s in pairs]
        return [e for e in out if e]

    train_ex, val_ex, held_ex = enc(train_pairs), enc(val_pairs), enc(heldout_pairs or [])
    log(f"sft: {len(train_ex)} train / {len(val_ex)} val / {len(held_ex)} held-out examples; "
        f"{model.num_params() / 1e6:.1f}M params; ctx {model.cfg.max_seq_len}")

    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tcfg = TrainConfig(steps=cfg.steps, batch_size=cfg.batch_size, seq_len=cfg.max_seq_len, lr=cfg.lr, min_lr=cfg.min_lr,
                       warmup=cfg.warmup, weight_decay=cfg.weight_decay, grad_clip=cfg.grad_clip, seed=cfg.seed,
                       init_from=cfg.init_from, out_dir=cfg.out_dir)
    opt = make_optimizer(model, tcfg)
    rng = random.Random(cfg.seed)
    history = []
    metrics = {"sft": True, "val_loss_before": masked_loss(model, val_ex, cfg.batch_size, pad_id)}
    if held_ex:
        metrics["heldout_loss_before"] = masked_loss(model, held_ex, cfg.batch_size, pad_id)
    log(f"sft: before training val {metrics['val_loss_before']:.3f}" +
        (f" held-out {metrics['heldout_loss_before']:.3f}" if held_ex else ""))
    best = float("inf")
    step, t0 = 0, time.time()
    model.train()
    while step < cfg.steps:
        for x, y in make_batches(train_ex, cfg.batch_size, rng, pad_id):
            if step >= cfg.steps:
                break
            for g in opt.param_groups:
                g["lr"] = lr_at(step, cfg)
            loss = model(x, y).loss
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            opt.step()
            step += 1
            if step % 10 == 0:
                log(f"step {step}/{cfg.steps} loss {loss.item():.4f} lr {lr_at(step, cfg):.2e} {time.time() - t0:.0f}s")
            if step % cfg.eval_every == 0 or step == cfg.steps:
                v = masked_loss(model, val_ex, cfg.batch_size, pad_id)
                h = masked_loss(model, held_ex, cfg.batch_size, pad_id) if held_ex else None
                history.append({"step": step, "train_loss": loss.item(), "val_loss": v, "heldout_loss": h,
                                "elapsed_s": round(time.time() - t0, 1)})
                log(f"eval step {step}: val {v:.4f}" + (f" held-out {h:.4f}" if h is not None else ""))
                m = {**metrics, "val_loss": v, "heldout_loss": h, "history": history}
                save_checkpoint(out / "last.pt", model, tok, step, m, tcfg)
                if v < best:
                    best = v
                    save_checkpoint(out / "best.pt", model, tok, step, m, tcfg)
    metrics.update({"best_val_loss": best, "history": history, "steps": step, "elapsed_s": round(time.time() - t0, 1),
                    "config": asdict(cfg)})
    (out / "sft_metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics
