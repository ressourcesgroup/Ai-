# Capability Status

Single source of truth for what exists. Updated every milestone.
**Implemented** = code + passing tests · **Experimental** = code exists, evidence pending · **Planned** = designed, not built · **Theoretical** = research direction only.

| Area | Component | Status | Evidence |
|---|---|---|---|
| Tokenizer | Byte-level BPE, code-aware pretokenizer, specials, persistence | Implemented | `tests/test_tokenizer.py` |
| Model | Dense transformer (RMSNorm, RoPE, GQA, SwiGLU) | Implemented (nano) | `tests/test_model.py` (shapes, causality, overfit) |
| Model | KV-cache inference | Implemented | cache == full forward to 1e-4 |
| Model | Sparse MoE (top-k, aux + z loss, load stats) | Experimental | tests + `experiments/exp001` |
| Model | Recurrent depth (shared-weight loops) | Experimental | tests + `experiments/exp001` |
| Model | Adaptive computation / halting | Planned | — |
| Model | Named domain experts | Planned (needs H-MoE-2) | — |
| Model | Multimodal encoders | Theoretical | — |
| Data | License-gated ingestion + manifest lineage | Implemented | `tests/test_data.py` |
| Data | Quality filter, exact + MinHash dedup, hash split | Implemented | `tests/test_data.py` |
| Data | Synthetic factory v0 (templated, verifier-gated) | Implemented | `tests/test_data.py` |
| Data | Seed corpus | Implemented — **16 files, ~51K train tokens; far too small for capability** | `data/seed_corpus` |
| Data | Large-scale licensed corpus | Planned | — |
| Training | Single-process pretraining, checkpoints, logs | Implemented | `tests/test_train_research.py` |
| Training | FIM, SFT, RL | Planned | — |
| Training | Distributed (FSDP/TP/PP/EP) | Planned | — |
| Verifiers | Luau lexer + structure checker | Implemented (heuristic, not the official compiler) | `tests/test_luau.py` |
| Verifiers | Security lint GM001–GM007 | Implemented (heuristic) | `tests/test_luau.py` |
| Verifiers | Official `luau-analyze` / runtime execution | Planned — blocked in the build sandbox (running downloaded binaries not permitted); CI job is the intended path | — |
| Eval | GRIB v0 (14 tasks, static grading) | Implemented | `tests/test_grib.py` |
| Eval | Contamination control | Implemented | `tests/test_grib.py` |
| Eval | Execution-based grading (GRIB v1) | Planned | — |
| Eval | Competitor runs (Claude, GPT, …) | Planned — **no competitor numbers exist** | — |
| Research | Experiment registry + promotion gate | Implemented | `tests/test_train_research.py` |
| Research | Automated weakness → hypothesis loop | Planned | — |
| Product | Inference server, project memory, agents, Studio plugin, IDE | Planned | — |
| Generative | GODMODE-3D/TEXTURE/ANIMATE/WORLD/AUDIO/UI/SPATIAL/OPTIMIZE | Theoretical | — |

## Measured results (milestone M1)

All on CPU (4 cores), nano scale. These measure the pipeline, not useful capability.

| Run | Result |
|---|---|
| `build-data` | 616 docs in → 327 kept (289 near-duplicate synthetic samples removed) → 51,308 train / 6,510 val tokens, vocab 1,640, 3.89 chars/token |
| `train` nano-dense (1.94M params, 400 steps) | val loss 7.28 → 0.506; train 0.17 (overfitting) |
| GRIB-v0-static, nano-dense | **0 / 14 (0%)**; reference oracle 14/14; empty answer 0/14 |
| exp001 dense vs MoE vs recurrent | see `experiments/exp001/README.md` |
