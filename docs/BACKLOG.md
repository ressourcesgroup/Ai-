# Engineering & Research Backlog

Prioritized by (expected impact × evidence) / (cost × risk). Each item names the evidence that motivated it.

| # | Item | Evidence | Impact | Cost |
|---|---|---|---|---|
| 1 | Official Luau toolchain in CI + runtime grading (GRIB v1) | GRIB v0 cannot see runtime bugs; heuristic checker needed 3 fixes for valid code (if-expressions, `?`/`>` type ends) | Very high | Medium |
| 2 | Data scale: census + ingestion of licensed Luau/code | 51K train tokens → train/val gap 0.17 vs 0.51 (memorization) | Very high | Medium |
| 3 | Synthetic diversity | MinHash removed 289/600 (48%) templated samples; nano model ignores prompt conditioning (asked for Gems/Wins, produced Score) | High | Medium |
| 4 | SFT loss masking to assistant tokens | Model currently trains on prompts too | Medium | Low |
| 5 | GRIB growth to 100+ tasks; replace contaminated tasks | 3–4 of 14 tasks flagged by 13-gram overlap with seed corpus | High | Medium |
| 6 | H-MoE-2: domain-supervised routing for named experts | Generic MoE has no domain specialization by construction | Medium | Medium |
| 7 | Vectorized MoE dispatch (sort-by-expert, grouped GEMM) | Per-expert Python loop; MoE wall time in exp001 | Medium | Medium |
| 8 | Adaptive computation (per-token halting) | Pending exp001 recurrent result | Medium | Medium |
| 9 | Lint GM002 taint tracking (argument → sink), cross-file remotes | GM002 accepts any validator call anywhere in the handler | Medium | Medium |
| 10 | Single-GPU path: bf16 autocast, `torch.compile`, grad accumulation | Required for M3 | High | Low |
| 11 | Resumable training (optimizer + data-loader state in checkpoint) | Current checkpoints store weights only | Medium | Low |

## Hypotheses queue

- **H-MoE-1** (exp001): a top-2-of-4 MoE beats dense at equal steps. *Running.*
- **H-REC-1** (exp001): 2 layers × 2 loops ≈ 4 dense layers at half the block parameters. *Running.*
- **H-MoE-2**: an auxiliary domain-routing loss yields measurable per-domain expert specialization (routing entropy per domain tag) without hurting val loss.
- **H-TTS-1**: verifier-filtered best-of-N raises GRIB pass rate monotonically in N for any model with nonzero pass@N.
- **H-TOK-1**: Roblox API identifiers as reserved tokens improve API-name accuracy at a fixed model size.
