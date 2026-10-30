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
