# exp001 — Dense vs sparse MoE vs recurrent depth (nano scale)

**Hypotheses.**
H-MoE-1: a 4-expert top-2 MoE (≈ same active params, +30% total) reaches lower val loss than dense at equal steps.
H-REC-1: 2 layers × 2 loops (same per-token compute, 58% of the parameters) matches the 4-layer dense model.

**Setup.** Same data (`build-data` defaults: 51K train / 6.5K val tokens), 300 steps, batch 16, seq 128, lr 3e-3 cosine, seeds {0,1,2}, 4-core CPU. Configs: `configs/nano-{dense,moe,recurrent}.json`. Reproduce:

```bash
python -m godmode build-data
python -m godmode experiment --name exp001 --hypothesis "..." \
  --configs configs/nano-dense.json configs/nano-moe.json configs/nano-recurrent.json --seeds 0 1 2 --steps 300
```

**Results** (best val loss; lower is better; registry entry `exp001-dense-moe-recurrent` in `experiments/registry.jsonl`):

| Arm | Params total / active | Val loss mean ± std | Per seed |
|---|---|---|---|
| dense (baseline) | 1.94M / 1.94M | **0.4904 ± 0.0081** | 0.5007, 0.4809, 0.4895 |
| MoE 4×top-2 | 2.53M / 1.94M | 0.4913 ± 0.0100 | 0.4867, 0.4820, 0.5052 |
| recurrent 2×2 | 1.13M / 1.13M | 0.5117 ± 0.0094 | 0.5102, 0.5239, 0.5011 |

**Promotion gate:** neither candidate promoted.
- MoE: Δ = −0.0009 vs noise 0.0091 → no detectable difference. **H-MoE-1 not supported** at this scale.
- Recurrent: worse by 0.021 (≈1.5× the pooled noise), about 4% higher loss. **H-REC-1 rejected as stated**: it does not match dense, though it gets within 4% using 58% of the parameters.

**Caveats.**
- At this data size every arm is memorization-limited (train loss ≪ val loss). Architecture differences are expected to show up only with more data. Repeat at micro scale (M3) before drawing conclusions.
- Wall times are **not comparable**: the dense runs overlapped with a test-suite run on the same CPU. Do not read the timing column as a speed result.
- 3 seeds give a weak test. The gate's "gain > pooled std" rule is a heuristic, not a significance test.

**Decision.** Keep dense as the baseline. Keep MoE and recurrent as experimental options. Re-run both at micro scale with ≥5 seeds once M3 data exists.
