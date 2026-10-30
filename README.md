# PROJECT GODMODE

An independent, from-scratch foundation-model research stack for Roblox and Luau development. It has its own tokenizer, architecture, weights, data pipeline, verifiers and benchmark. It is not a wrapper around any external model.

> **Honest status (milestone M1):** the full pipeline (data → tokenizer → model → training → benchmark → experiment registry) runs end to end and is covered by 76 tests. The trained model is **nano scale (~2M parameters, ~51K training tokens, CPU-trained)** and scores **0%** on our benchmark, GRIB v0. That is a real baseline, not a capable assistant. See [docs/STATUS.md](docs/STATUS.md) for exactly what is implemented, experimental, planned or theoretical.

## What's here

| Path | What |
|---|---|
| `godmode/tokenizer` | Byte-level BPE trained from scratch, code-aware pre-tokenization |
| `godmode/model` | Decoder-only transformer: RMSNorm, RoPE, GQA, SwiGLU, KV cache, sparse MoE, recurrent depth |
| `godmode/data` | License-gated ingestion, quality filter, exact + MinHash dedup, verifier-gated synthetic data |
| `godmode/luau` | Luau lexer, structural checker, Roblox security lint (GM001–GM007) |
| `godmode/train` | Pretraining loop, cosine LR, checkpoints with tokenizer integrity checks |
| `godmode/eval` | GRIB benchmark harness, contamination control, model adapters |
| `godmode/research` | Append-only experiment registry, evidence-based promotion gate |
| `data/seed_corpus` | Original MIT-licensed Luau (16 files) |
| `benchmarks/grib` | GRIB v0 tasks with reference solutions |
| `configs/` | Model configs: nano dense / MoE / recurrent |
| `experiments/` | Registry and write-ups of controlled experiments |
| `docs/` | [Architecture](docs/ARCHITECTURE.md) · [Diagrams](docs/DIAGRAMS.md) · [Roadmap](docs/ROADMAP.md) · [Status](docs/STATUS.md) · [Backlog](docs/BACKLOG.md) |

## Quickstart

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[dev]"
pytest -q                                   # 76 tests

python -m godmode build-data                # corpus + synthetic -> filtered, deduped, tokenized (runs/data)
python -m godmode train --steps 400         # ~75 s on 4 CPU cores
python -m godmode generate --ckpt runs/nano-dense/best.pt \
    --prompt $'<|user|>Create leaderstats with IntValues: Gems, Wins.\n<|assistant|>' --temperature 0
python -m godmode grib --adapter ckpt:runs/nano-dense/best.pt --train-texts runs/data/train_texts.json
python -m godmode experiment --name my-exp --hypothesis "..." \
    --configs configs/nano-dense.json configs/nano-moe.json --seeds 0 1 2
```

## Principles

1. Evidence over assertion: nothing is claimed without a reproducible test or benchmark.
2. Every architecture idea is an experiment against a baseline, with multiple seeds.
3. Verifiers (checkers, linters, test runners) are the core asset. They filter data, grade benchmarks and will supply RL rewards.
4. Only legally usable, provenance-tracked data.
5. Generated code must be server-authoritative and secure. GODMODE does not produce exploits.

## License

MIT. See [LICENSE](LICENSE).
