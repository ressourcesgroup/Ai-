# Training GODMODE on cloud GPUs

Status: **the multi-GPU code path is tested only as 2-process CPU/gloo DDP** (`tests/test_train_research.py::test_distributed_data_parallel_two_processes`). It has never run on a real GPU. The first GPU run is a smoke test (below) before you spend real budget.

## 1. Machine

Any Linux GPU box with CUDA 12 and bf16 support (A100, H100, L40S, RTX 4090/5090). Suggested starting point: **1 node × 8 GPUs** for `base-300m`, or **1 GPU** for `small-cpu`-sized ablations.

```bash
git clone <your repo>  godmode && cd godmode     # or unzip godmode.zip
pip install torch            # CUDA build from pytorch.org for your driver
pip install -e ".[dev]"
pytest -q                     # must pass before training
```

## 2. Data

```bash
python -m godmode fetch-data roblox-luau-corpus          # Roblox/luau_corpus, MIT, ~37M chars
python -m godmode build-data --sources data/seed_corpus data/external/roblox_luau_corpus \
       --vocab-size 32768 --synthetic 2000 --out runs/data-full
cat runs/data-full/summary.json                          # check leakage_13gram, token counts
```

Data reality: Roblox's opt-in corpus plus our seed corpus is roughly **10M tokens**. That is enough to train `small-cpu` (12.6M params), but **~30× too little for a compute-optimal 300M model** (≈6B tokens). Before `base-300m` is worth its cost you need general code pretraining data. Options, each needing *your* acceptance of the provider's terms:

- **The Stack v2** (bigcode): has per-file license metadata. Use the permissive subset and the Lua slice first. It requires a Hugging Face login, acceptance of the terms, and Software Heritage bulk-download access.
- **Roblox Creator Docs** (github.com/Roblox/creator-docs, CC BY 4.0): requires attribution. Add a `SOURCE.json` with `"license": "CC-BY-4.0"`. GitHub was unreachable from our build sandbox, so this has not been ingested.

Every source goes in its own directory with a `SOURCE.json`. The loader refuses anything without an allow-listed license.

## 3. Smoke test (≈5 minutes, do this first)

```bash
torchrun --nproc_per_node=8 -m godmode train --data runs/data-full --config configs/small-cpu.json \
    --steps 50 --eval-every 25 --batch-size 32 --seq-len 512 --out runs/smoke
```

Expected: `"world_size": 8`, `"precision": "bf16"`, and val loss falling. If it fails, the error is real: fix it before scaling.

## 4. Main runs

```bash
# 12.6M model, 1 GPU: architecture ablations in minutes
python -m godmode train --data runs/data-full --config configs/small-cpu.json --steps 20000 \
    --batch-size 64 --seq-len 512 --lr 1e-3 --out runs/small

# 300M model, 8 GPUs (only after adding general-code data)
torchrun --nproc_per_node=8 -m godmode train --data runs/data-full --config configs/base-300m.json \
    --steps 100000 --batch-size 16 --grad-accum 4 --seq-len 2048 --lr 3e-4 --out runs/base-300m
```

Tokens per step = batch × seq_len × grad_accum × GPUs. For the 300M command above that is 16 × 2048 × 4 × 8 ≈ 1.05M tokens per step, so 100K steps ≈ 105B tokens. That is above Chinchilla-optimal; shorten `--steps` to fit your budget.

Rough cost model (an estimate, not a measurement): training FLOPs ≈ 6 × params × tokens. 300M params × 6B tokens ≈ 1.1×10¹⁹ FLOPs ≈ 1 hour on 8×H100 at 40% utilization.

## 5. After training

```bash
python -m godmode grib --adapter ckpt:runs/small/best.pt --train-texts runs/data-full/train_texts.json
python -m godmode.server --ckpt runs/small/best.pt        # the Studio plugin then uses it
```

Promote a checkpoint only through the promotion gate (multiple seeds, no regressions). See `docs/ARCHITECTURE.md` §12.

## Not yet implemented (needed beyond ~1B params)

FSDP sharding, tensor/pipeline/expert parallelism, resumable optimizer + data-loader state, and streaming data loading (the current loader holds all tokens in RAM, which is fine up to a few billion tokens with uint16).
