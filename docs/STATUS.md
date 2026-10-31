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
| Training | SFT on verified prompt→full-script pairs (assistant-only loss, held-out families) | Implemented | `tests/test_thinking_sft.py`, `runs/sft-scripts/sft_metrics.json` |
| Training | FIM, RL | Planned | — |
| Training | Distributed (FSDP/TP/PP/EP) | Planned | — |
| Verifiers | Luau lexer + structure checker | Implemented (heuristic, not the official compiler) | `tests/test_luau.py` |
| Verifiers | Security lint GM001–GM007 | Implemented (heuristic) | `tests/test_luau.py` |
| Verifiers | Official `luau-analyze` / runtime execution | Planned — blocked in the build sandbox (running downloaded binaries not permitted); CI job is the intended path | — |
| Eval | GRIB v0 (14 tasks, static grading) | Implemented | `tests/test_grib.py` |
| Eval | Contamination control | Implemented | `tests/test_grib.py` |
| Eval | Script-completion eval (fresh prompts, held-out families, task-API check) | Implemented (static proxy, not playtests) | `godmode/eval/script_eval.py`, `runs/eval/` |
| Eval | Execution-based grading (GRIB v1) | Planned | — |
| Eval | Competitor runs (Claude, GPT, …) | Planned — **no competitor numbers exist** | — |
| Research | Experiment registry + promotion gate | Implemented | `tests/test_train_research.py` |
| Research | Automated weakness → hypothesis loop | Planned | — |
| Product | Local inference server + Studio plugin, thinking levels low…max | Implemented | `tests/test_server.py`, `plugin/` |
| Product | Online mode (Docker image, token-required public bind) | Implemented — image built and `/health` checked in the sandbox; **not deployed anywhere by us** | `Dockerfile`, `docs/ONLINE_MODE.md` |
| Product | Project memory, agents, IDE | Planned | — |
| Generative | GODMODE-3D/TEXTURE/ANIMATE/WORLD/AUDIO/UI/SPATIAL/OPTIMIZE | Theoretical | — |

## Measured results (milestone M1)

All on CPU (4 cores), nano scale. These measure the pipeline, not useful capability.

| Run | Result |
|---|---|
| `build-data` | 616 docs in → 327 kept (289 near-duplicate synthetic samples removed) → 51,308 train / 6,510 val tokens, vocab 1,640, 3.89 chars/token |
| `train` nano-dense (1.94M params, 400 steps) | val loss 7.28 → 0.506; train 0.17 (overfitting) |
| GRIB-v0-static, nano-dense | **0 / 14 (0%)**; reference oracle 14/14; empty answer 0/14 |
| exp001 (3 seeds, 300 steps) | dense 0.490±0.008 · MoE 0.491±0.010 (no difference) · recurrent 0.512±0.009 (worse, 58% params). Gate promoted neither. [details](../experiments/exp001/README.md) |

## Measured results (milestone M2: script SFT)

CPU (4 cores). Model: GodmodeLM 12.6M params, SFT from `runs/small-full2` for 1500 steps (batch 16, ctx 768, ~60 min)
on 3,617 verified full scripts from 32 families; 3 families (`speed_pad`, `name_tag`, `camera_shake`) never trained on.

| Loss | before | after |
|---|---|---|
| val (seen families, unseen samples) | 2.196 | **0.012** |
| held-out families | 1.786 | **2.832 (got worse — overfit to seen families)** |

`godmode eval-scripts` (fresh seed 9999; 1 prompt per seen family, 3 per held-out family; real server code path).
*complete* = passes verifier, correct script class, ≥50% of the reference's services **and** ≥50% of its task-specific API names.

| Checkpoint · level | seen complete (n=32) | held-out complete (n=9) | mean s/prompt |
|---|---|---|---|
| pretrain only (`small-full2`) · low | 0% | 0% | 1.3 |
| SFT · low | **81%** | 22% | 1.6 |
| SFT · medium | **94%** | 11% | 3.3 |
| SFT · high (held-out only) | — | 22% | 8.3 |

Reading: the model now writes whole, placed scripts for task types it was trained on; for **new task types it mostly
writes a valid script for a different task** (e.g. a katana when asked for a speed pad). Higher thinking levels help on
seen tasks (more candidates → verifier picks a passing one) but do not fix generalisation. These are static proxies, not
playtests.

GRIB-v0-static (21 tasks): pretrain ckpt 1/21 · SFT ckpt 1/21 · full system (router + tools + SFT model) **4/21 (19%)**.
