# Roadmap

Each phase has exit criteria that must be shown by tests or benchmarks. Dates are deliberately omitted until compute is provisioned. Phases overlap where dependencies allow.

## Milestone M1 — "Nano foundation" ✅ (this commit)

Exit criteria, all met and tested:
- [x] Tokenizer trained from scratch, lossless, deterministic
- [x] Transformer with MoE and recurrent options; causality and KV-cache tests
- [x] License-gated data pipeline with dedup, filtering, lineage manifest
- [x] Verifier-gated synthetic data v0
- [x] Training loop with checkpoints whose tokenizer integrity is verified on load
- [x] GRIB v0 harness + 14 tasks; oracle 100%, empty 0%, contamination control
- [x] Experiment registry, promotion gate, first multi-seed architecture experiment

## Milestone M2 — "Real verifiers" (next)

1. Official Luau toolchain in CI (`luau-analyze` type checking, `luau` execution). Wire it into the data filter and GRIB as an optional, stronger verifier.
2. Roblox API mock layer in Luau (Players, RemoteEvent/Function, DataStore with injected failures, task scheduler). This enables GRIB v1 execution-based grading with hidden tests.
3. Expand GRIB to ≥100 tasks, with ≥20% adversarial debugging tasks; rotate out contaminated tasks.
4. Cross-file lint: remote producer/consumer matching.

Exit: GRIB v1 grades by running code; reference 100%, empty 0%, deliberately buggy references fail.

## Milestone M3 — "Micro model on real data"

1. Phase 0 measurement: how many tokens of permissively licensed Luau and general code we can legally obtain.
2. Ingest at that scale with the existing gates. Streaming tokenization to sharded `.npy`.
3. Single-GPU training path (bf16, `torch.compile`, gradient accumulation). Train a ~25M-param model.
4. FIM training. A completion-quality eval (exact-match on held-out function bodies).

Exit: the micro model beats the nano model and an n-gram baseline on held-out FIM exact-match; scaling curve over ≥3 model sizes.

## Phase map (from the project brief)

| Phase | Content | Depends on |
|---|---|---|
| 0 Research & feasibility | Data census, hardware plan, benchmark design | — (in progress: architecture spec done) |
| 1 Independent foundation | Tokenizer, model, training, inference, checkpoints | **M1 done at nano scale**; M3 for real scale |
| 2 Intelligence development | SFT, RL with verifiable rewards, MoE/recurrent decisions, test-time search | M2 verifiers |
| 3 Roblox mastery | API-aware generation, docs index (license permitting), GRIB v1+ | M2, M3 |
| 4 Project intelligence | Dependency graph, semantic index, decision log | Luau front end (M2) |
| 5 Multimodal | Synthetic rendered UI/scene data, encoders | Phase 3 |
| 6 Generative ecosystem | Program-generating WORLD/UI first; mesh/audio later | Phase 5 |
| 7 Dev environment | Desktop app, Studio plugin bridge | Phase 4 |
| 8 Agents | Supervisor, file ownership, autonomy levels | Phases 4, 7 |
| 9 Distributed infra | FSDP → multi-node → dedicated clusters | Funding/hardware |
| 10 Frontier research | Continuous loop at scale | All |
