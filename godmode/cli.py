"""Command line entry point: `python -m godmode <command>`."""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict
from pathlib import Path

import numpy as np

SEED_CORPUS = "data/seed_corpus"
GRIB_TASKS = "benchmarks/grib/tasks_v0.jsonl"


def cmd_build_data(args) -> None:
    from .data import build_corpus, generate, load_source_dir, pack, split_documents, write_manifest
    from .tokenizer import BPETokenizer

    docs = []
    for d in args.sources:
        docs += load_source_dir(d)
    samples, rejected = generate(args.synthetic, seed=args.seed)
    docs += [s.to_document() for s in samples]
    kept, report = build_corpus(docs)
    train_docs, val_docs = split_documents(kept, args.val_fraction)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tok = BPETokenizer.train([d.text for d in train_docs], vocab_size=args.vocab_size)
    tok.save(out / "tokenizer.json")
    train_ids, val_ids = pack(train_docs, tok), pack(val_docs, tok)
    np.save(out / "train.npy", train_ids)
    np.save(out / "val.npy", val_ids)
    write_manifest(kept, report, out / "manifest.json")
    (out / "train_texts.json").write_text(json.dumps([d.text for d in train_docs]))
    summary = {
        "documents": len(kept), "train_docs": len(train_docs), "val_docs": len(val_docs),
        "synthetic_rejected_by_verifier": rejected, "filter": asdict(report),
        "vocab_size": tok.vocab_size, "train_tokens": int(len(train_ids)), "val_tokens": int(len(val_ids)),
        "chars_per_token": round(sum(len(d.text) for d in train_docs) / max(1, len(train_ids)), 3),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


def _load_data(data_dir: str):
    from .tokenizer import BPETokenizer

    d = Path(data_dir)
    return BPETokenizer.load(d / "tokenizer.json"), np.load(d / "train.npy"), np.load(d / "val.npy")


def _model_config(path: str, tok):
    from .model import ModelConfig

    raw = json.loads(Path(path).read_text())
    raw["vocab_size"] = tok.vocab_size
    return ModelConfig.from_dict(raw)


def cmd_train(args) -> None:
    from .train import TrainConfig, train

    tok, tr, va = _load_data(args.data)
    mcfg = _model_config(args.config, tok)
    tcfg = TrainConfig(steps=args.steps, batch_size=args.batch_size, seq_len=args.seq_len, lr=args.lr,
                       seed=args.seed, out_dir=args.out, eval_every=args.eval_every)
    result = train(mcfg, tcfg, tok, tr, va)
    result.pop("history")
    print(json.dumps(result, indent=2))


def cmd_generate(args) -> None:
    import torch

    from .train import load_checkpoint

    model, tok, _ = load_checkpoint(args.ckpt)
    ids = tok.encode(args.prompt, allow_special=True)
    g = torch.Generator().manual_seed(args.seed)
    out = model.generate(torch.tensor([ids]), args.max_new_tokens, temperature=args.temperature, top_k=args.top_k,
                         stop_ids={tok.token_id("<|eos|>")}, generator=g)
    print(tok.decode(out[0].tolist()))


def cmd_grib(args) -> None:
    from .eval import grib

    tasks = grib.load_tasks(args.tasks)
    texts = json.loads(Path(args.train_texts).read_text()) if args.train_texts else []
    contaminated = grib.contaminated_ids(grib.contamination_report(tasks, texts)) if texts else set()
    if args.adapter == "reference":
        adapter = grib.reference_adapter(tasks)
    elif args.adapter == "empty":
        adapter = grib.empty_adapter
    elif args.adapter.startswith("ckpt:"):
        adapter = grib.checkpoint_adapter(args.adapter[5:])
    else:
        raise SystemExit(f"unknown adapter {args.adapter!r}")
    report = grib.run(tasks, adapter, args.adapter, contaminated)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
    report.pop("results")
    print(json.dumps(report, indent=2))


def cmd_experiment(args) -> None:
    """Controlled architecture comparison: same data, steps, seeds; multiple seeds per arm."""
    from .research import ExperimentRecord, Registry, promotion_gate
    from .train import TrainConfig, train

    tok, tr, va = _load_data(args.data)
    arms = {Path(c).stem: _model_config(c, tok) for c in args.configs}
    per_arm: dict[str, dict[str, list[float]]] = {}
    details: dict[str, dict] = {}
    for name, mcfg in arms.items():
        per_arm[name] = {"val_loss": [], "wall_time_s": []}
        for seed in args.seeds:
            tcfg = TrainConfig(steps=args.steps, batch_size=args.batch_size, seq_len=args.seq_len, lr=args.lr,
                               seed=seed, out_dir=f"runs/{args.name}/{name}/seed{seed}", eval_every=args.steps)
            res = train(mcfg, tcfg, tok, tr, va, log=lambda *_: None)
            per_arm[name]["val_loss"].append(res["best_val_loss"])
            per_arm[name]["wall_time_s"].append(res["wall_time_s"])
            details[name] = {"params_total": res["params_total"], "params_active": res["params_active"], "config": mcfg.to_dict()}
            print(f"{name} seed={seed} val_loss={res['best_val_loss']} time={res['wall_time_s']}s")

    baseline = Path(args.configs[0]).stem
    summary = {
        name: {"val_loss_mean": round(statistics.mean(m["val_loss"]), 4),
               "val_loss_std": round(statistics.pstdev(m["val_loss"]), 4),
               "wall_time_mean_s": round(statistics.mean(m["wall_time_s"]), 1),
               **{k: v for k, v in details[name].items() if k != "config"}}
        for name, m in per_arm.items()
    }
    gates = {
        name: asdict(promotion_gate(per_arm[baseline], per_arm[name], "val_loss", {"val_loss": True, "wall_time_s": True},
                                    guard_tolerance=args.time_tolerance))
        for name in per_arm if name != baseline
    }
    record = ExperimentRecord(
        name=args.name, hypothesis=args.hypothesis,
        config={"arms": {k: v["config"] for k, v in details.items()}, "steps": args.steps, "batch_size": args.batch_size,
                "seq_len": args.seq_len, "lr": args.lr, "data": args.data},
        seeds=args.seeds, metrics={"per_seed": per_arm, "summary": summary, "gate_vs_" + baseline: gates},
        conclusion=args.conclusion,
    )
    Registry(args.registry).append(record)
    print(json.dumps({"summary": summary, "gates": gates}, indent=2))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="godmode", description="PROJECT GODMODE research stack")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build-data", help="ingest, filter, dedup, split, train tokenizer, pack tokens")
    b.add_argument("--sources", nargs="+", default=[SEED_CORPUS])
    b.add_argument("--synthetic", type=int, default=600)
    b.add_argument("--vocab-size", type=int, default=2048)
    b.add_argument("--val-fraction", type=float, default=0.1)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--out", default="runs/data")
    b.set_defaults(fn=cmd_build_data)

    t = sub.add_parser("train", help="pretrain a model from a config")
    t.add_argument("--data", default="runs/data")
    t.add_argument("--config", default="configs/nano-dense.json")
    t.add_argument("--steps", type=int, default=500)
    t.add_argument("--batch-size", type=int, default=16)
    t.add_argument("--seq-len", type=int, default=128)
    t.add_argument("--lr", type=float, default=3e-3)
    t.add_argument("--eval-every", type=int, default=100)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--out", default="runs/nano-dense")
    t.set_defaults(fn=cmd_train)

    g = sub.add_parser("generate", help="sample from a checkpoint")
    g.add_argument("--ckpt", required=True)
    g.add_argument("--prompt", required=True)
    g.add_argument("--max-new-tokens", type=int, default=200)
    g.add_argument("--temperature", type=float, default=0.8)
    g.add_argument("--top-k", type=int, default=40)
    g.add_argument("--seed", type=int, default=0)
    g.set_defaults(fn=cmd_generate)

    r = sub.add_parser("grib", help="run the GRIB benchmark")
    r.add_argument("--adapter", default="reference", help="reference | empty | ckpt:<path>")
    r.add_argument("--tasks", default=GRIB_TASKS)
    r.add_argument("--train-texts", default=None, help="train_texts.json from build-data, for contamination checks")
    r.add_argument("--out", default=None)
    r.set_defaults(fn=cmd_grib)

    e = sub.add_parser("experiment", help="multi-seed controlled comparison of model configs")
    e.add_argument("--name", required=True)
    e.add_argument("--hypothesis", required=True)
    e.add_argument("--conclusion", default="")
    e.add_argument("--configs", nargs="+", required=True, help="first config is the baseline")
    e.add_argument("--data", default="runs/data")
    e.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    e.add_argument("--steps", type=int, default=300)
    e.add_argument("--batch-size", type=int, default=16)
    e.add_argument("--seq-len", type=int, default=128)
    e.add_argument("--lr", type=float, default=3e-3)
    e.add_argument("--time-tolerance", type=float, default=1e9, help="allowed wall-time regression (s)")
    e.add_argument("--registry", default="experiments/registry.jsonl")
    e.set_defaults(fn=cmd_experiment)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
