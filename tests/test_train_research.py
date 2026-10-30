import json

import numpy as np

from godmode.model import ModelConfig
from godmode.research import ExperimentRecord, Registry, promotion_gate
from godmode.tokenizer import BPETokenizer
from godmode.train import TrainConfig, load_checkpoint, train
from godmode.train.trainer import lr_at


def test_lr_schedule():
    cfg = TrainConfig(steps=100, warmup=10, lr=1.0, min_lr=0.1)
    assert lr_at(0, cfg) < lr_at(9, cfg) == 1.0
    assert abs(lr_at(99, cfg) - 0.1) < 1e-3


def test_train_reduces_loss_and_checkpoint_roundtrips(tmp_path):
    text = "local Players = game:GetService(\"Players\")\nprint(Players)\n" * 50
    tok = BPETokenizer.train([text], vocab_size=300)
    ids = np.array(tok.encode(text) * 4, dtype=np.uint16)
    mcfg = ModelConfig(vocab_size=tok.vocab_size, d_model=32, n_layers=2, n_heads=4, n_kv_heads=2, max_seq_len=64)
    tcfg = TrainConfig(steps=40, batch_size=4, seq_len=32, warmup=5, eval_every=20, eval_batches=2, out_dir=str(tmp_path))
    res = train(mcfg, tcfg, tok, ids, ids, log=lambda *_: None)
    assert res["history"][-1]["val_loss"] < res["history"][0]["val_loss"] * 0.5
    model, tok2, ckpt = load_checkpoint(tmp_path / "best.pt")
    assert tok2.fingerprint() == tok.fingerprint() and ckpt["step"] >= 0
    assert (tmp_path / "log.jsonl").read_text().count("\n") == len(res["history"])


def test_registry_append_only(tmp_path):
    reg = Registry(tmp_path / "r.jsonl")
    reg.append(ExperimentRecord("e", "h", {"a": 1}, [0], {"m": 1}))
    reg.append(ExperimentRecord("e", "h2", {}, [1], {}))
    assert len(reg.find("e")) == 2
    assert all("git" in json.loads(l) for l in (tmp_path / "r.jsonl").read_text().splitlines())


def test_promotion_gate():
    lib = {"val_loss": True, "security_failures": True}
    base = {"val_loss": [1.00, 1.01, 0.99], "security_failures": [0, 0, 0]}
    better = {"val_loss": [0.80, 0.81, 0.79], "security_failures": [0, 0, 0]}
    noisy = {"val_loss": [0.99, 1.02, 0.98], "security_failures": [0, 0, 0]}
    unsafe = {"val_loss": [0.80, 0.81, 0.79], "security_failures": [1, 2, 1]}
    assert promotion_gate(base, better, "val_loss", lib).promote
    assert not promotion_gate(base, noisy, "val_loss", lib).promote
    assert not promotion_gate(base, unsafe, "val_loss", lib).promote  # one metric up, a guard down
    assert not promotion_gate({"val_loss": [1.0]}, {"val_loss": [0.1]}, "val_loss", lib).promote  # single seed
    # Regressions found in review: tiny or noisy gains must not pass.
    assert not promotion_gate({"val_loss": [0.5000, 0.5001]}, {"val_loss": [0.4999, 0.5000]}, "val_loss", lib).promote
    assert not promotion_gate({"val_loss": [0.50, 0.52]}, {"val_loss": [0.48, 0.50]}, "val_loss", lib).promote
    # Zero variance: only min_effect decides.
    assert not promotion_gate({"val_loss": [0.5, 0.5]}, {"val_loss": [0.499, 0.499]}, "val_loss", lib, min_effect=0.01).promote
    assert promotion_gate({"val_loss": [0.5, 0.5]}, {"val_loss": [0.4, 0.4]}, "val_loss", lib, min_effect=0.01).promote


def test_train_reports_test_loss_and_supports_grad_accum(tmp_path):
    text = "local x = game:GetService(\"Players\")\nprint(x)\n" * 50
    tok = BPETokenizer.train([text], vocab_size=300)
    ids = np.array(tok.encode(text) * 4, dtype=np.uint16)
    mcfg = ModelConfig(vocab_size=tok.vocab_size, d_model=32, n_layers=2, n_heads=4, n_kv_heads=2, max_seq_len=64)
    tcfg = TrainConfig(steps=10, batch_size=2, seq_len=32, warmup=2, eval_every=5, eval_batches=2, grad_accum=2,
                       device="cpu", out_dir=str(tmp_path))
    res = train(mcfg, tcfg, tok, ids, ids, log=lambda *_: None, test_tokens=ids)
    assert "test_loss" in res and res["world_size"] == 1 and res["precision"] == "fp32"


def test_distributed_data_parallel_two_processes(tmp_path):
    """Real 2-process DDP run on CPU (gloo) through the CLI."""
    import subprocess
    import sys

    text = "local x = game:GetService(\"Players\")\nprint(x)\n" * 80
    tok = BPETokenizer.train([text], vocab_size=300)
    ids = np.array(tok.encode(text) * 4, dtype=np.uint16)
    data = tmp_path / "data"
    data.mkdir()
    tok.save(data / "tokenizer.json")
    for split in ("train", "val", "test"):
        np.save(data / f"{split}.npy", ids)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"vocab_size": 0, "d_model": 32, "n_layers": 2, "n_heads": 4, "n_kv_heads": 2, "max_seq_len": 64,
                               "n_experts": 2, "top_k": 1, "moe_every": 2}))
    proc = subprocess.run(
        [sys.executable, "-m", "torch.distributed.run", "--nproc_per_node=2", "-m", "godmode", "train", "--data", str(data),
         "--config", str(cfg), "--steps", "6", "--eval-every", "3", "--seq-len", "32", "--batch-size", "2",
         "--out", str(tmp_path / "run")],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert '"world_size": 2' in proc.stdout
    assert (tmp_path / "run" / "best.pt").exists()
