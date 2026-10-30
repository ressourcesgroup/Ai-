# PROJECT GODMODE — Master Architecture Specification

Version 0.1 · Status legend used throughout: **[IMPLEMENTED]** code exists and is tested, **[EXPERIMENTAL]** code exists, evidence still being gathered, **[PLANNED]** designed, not built, **[THEORETICAL]** research direction, no design commitment yet. The authoritative per-component status table is [STATUS.md](STATUS.md).

---

## 1. Mission and engineering principles

Build an independent foundation model for Roblox/Luau development: our own tokenizer, architecture, weights, data pipeline, training, inference, and evaluation. External models may be used only as documented teachers, data generators or benchmark competitors (§4.6). They never serve production traffic under the GODMODE name.

Principles, in priority order:

1. **Evidence over assertion.** A capability exists when a test or benchmark shows it. No result is reported without the command that reproduces it.
2. **Baselines first.** Every architectural idea is an experiment against a fixed baseline, with multiple seeds (§11.4).
3. **Verifiers are the core asset.** Luau checkers, linters, test runners and Roblox simulators provide the reward signal, the data filter and the benchmark grader. Investing in verifiers compounds.
4. **Scale progressively.** The same code paths run on a laptop CPU (nano scale) and, later, a cluster. Nothing assumes compute that has not been provisioned.
5. **Safety is architectural.** It lives in permissions, sandboxes, gates and audit logs, not in prompts.

## 2. System overview

Diagrams are in [DIAGRAMS.md](DIAGRAMS.md). The system has eight layers:

| Layer | Responsibility | Package / location |
|---|---|---|
| Data | Ingestion, licensing, filtering, dedup, synthetic generation | `godmode/data` |
| Tokenization | Byte-level BPE trained on our corpus | `godmode/tokenizer` |
| Model | Decoder-only transformer, MoE, recurrent depth | `godmode/model` |
| Training | Pretraining → SFT → RL → continual learning | `godmode/train` |
| Verification | Luau lexer, structure checker, security lint (→ type checker, runtime) | `godmode/luau` |
| Evaluation | GRIB benchmark, contamination control, competitor adapters | `godmode/eval`, `benchmarks/` |
| Research | Experiment registry, promotion gate, improvement loop | `godmode/research`, `experiments/` |
| Product | Inference server, project memory, agents, Studio bridge, IDE | planned (§7–§10) |

## 3. Foundation model

### 3.1 Tokenizer [IMPLEMENTED]

Byte-level BPE (`godmode/tokenizer/bpe.py`), trained from scratch on our corpus:

- **Lossless.** It uses a 256-byte base alphabet, so any UTF-8 round-trips (tested).
- **Code-aware pre-tokenization.** `\n` plus leading indentation is a single chunk, so the model sees block structure as a few tokens. Identifiers carry one leading space. Punctuation runs can merge, so `..=`, `~=` and `::` can each become one token. Digits are grouped in threes to keep arithmetic token boundaries regular.
- **Special tokens** for document boundaries, fill-in-the-middle (`<|fim_prefix|>` …) and chat roles.
- **Determinism and integrity.** Training is deterministic. Every checkpoint embeds the tokenizer and its SHA-256 fingerprint, and loading verifies it.

Open questions (backlog): the target vocabulary at scale (32k–64k is typical for code models), whether Roblox API names deserve reserved tokens, and FIM rate during pretraining.

### 3.2 Core network [IMPLEMENTED at nano scale]

Decoder-only transformer (`godmode/model/transformer.py`). Each choice is a well-replicated result, not a novelty:

| Component | Choice | Reason |
|---|---|---|
| Normalization | Pre-norm RMSNorm | Stable at depth, cheaper than LayerNorm |
| Positions | RoPE | Relative, extrapolates better; works with KV cache |
| Attention | Grouped-query attention | KV-cache memory ∝ `n_kv_heads`, important for long code context |
| FFN | SwiGLU, d_ff ≈ 8/3·d | Consistently better than GELU MLP at equal FLOPs |
| Init | N(0, 0.02), residual projections scaled by 1/√(2·depth) | GPT-2 practice for deep residual stacks |
| Embeddings | Tied input/output | Saves parameters at small scale (revisit at >1B) |
| Inference | KV cache, greedy / temperature / top-k | Cache output matches a full forward pass to 1e-4 (tested) |

Scaling plan (parameter counts are targets, not claims):

| Tier | Params | Purpose | Hardware |
|---|---|---|---|
| nano | ~2M | Pipeline validation, CI | CPU (today) |
| micro | ~25M | Architecture ablations | 1 consumer GPU |
| small | ~350M | First useful completions; scaling-law fits | 1–8 GPUs |
| base | 1–3B | Local-inference product model | 8–64 GPUs |
| large | 7B+ dense or ~30B MoE | Frontier-for-domain target | cluster (§6) |

Compute-optimal token budgets follow the Chinchilla rule of ≈20 tokens/parameter (Hoffmann et al. 2022). Training FLOPs ≈ 6·N·D. Example: a 1B model on 20B tokens is 1.2×10²⁰ FLOPs. At an assumed 40% MFU on 8×H100 (≈3.2×10¹⁵ effective FLOP/s), that is about 10.5 hours. These are estimates to plan budgets, not measurements.

### 3.3 Sparse Mixture-of-Experts [EXPERIMENTAL]

`MoE` layer: a softmax router, top-k selection with renormalized weights, SwiGLU experts, a Switch-Transformer load-balancing loss (Fedus et al. 2021) and a router z-loss (Zoph et al. 2022). Per-expert token counts are logged during training. Every `moe_every`-th block is sparse.

**On "named experts" (Luau Expert, Security Expert, …).** Learned token-level routers do not, by default, specialize along human-defined domains. Specialization is emergent and usually lexical. Two research paths make named experts real:

1. **Domain-supervised routing.** An auxiliary loss pushes tokens from tagged data (security examples, networking code, …) toward designated experts. Hypothesis H-MoE-2 in [BACKLOG.md](BACKLOG.md).
2. **Branch-Train-Merge / expert upcycling** (Li et al. 2022; Komatsuzaki et al. 2022). Fork the dense model, continue training each copy on a domain slice, then merge as experts. This also gives the "create a new expert for a capability gap" path its mechanism (§12).

Named experts are therefore **[PLANNED]**, gated on experiment evidence. The current MoE is generic.

### 3.4 Recurrent reasoning and test-time compute [EXPERIMENTAL]

- **Recurrent depth** (`n_loops`): the block stack is reapplied with shared weights (Universal Transformers; Dehghani et al. 2019). It gives more compute per token without more parameters. Experiment exp001 tests whether it matches a deeper dense model.
- **Adaptive computation** (ACT-style halting per token): [PLANNED] after exp001.
- **Verifier-guided test-time search:** [PLANNED, Phase 2]. Sample N candidates, run the verifier stack (structure → lint → type-check → tests), and return the best verified candidate with a verification summary. Because the verifiers are executable, this scales reliably with compute, unlike an unverified chain of thought.
- **Reasoning disclosure policy:** users receive plans, evidence and verification summaries, not raw internal traces.

### 3.5 Multimodal intelligence [THEORETICAL → PLANNED Phase 5]

Planned approach: modality encoders project into the language model's embedding space (LLaVA-style adapters first, since they are cheap and well understood). Native early fusion is a later research track.

| Modality | Candidate encoder | First task |
|---|---|---|
| Screenshots / UI | ViT trained from scratch on rendered Roblox UIs (synthetic: we generate the UI, so labels are free) | screenshot → ScreenGui hierarchy |
| 3D scenes | Point/mesh encoder over Instance trees (position, size, class, material) | scene → spatial Q&A, placement |
| Video | Frame encoder + temporal pooling | gameplay bug localization |
| Audio | Spectrogram transformer | SFX tagging |

Key insight: Roblox scenes are **programmatic**, so we can render unlimited perfectly labeled (scene-graph, image) pairs. That makes the visual data problem tractable without scraping.

## 4. Data and training architecture

### 4.1 Data sources and licensing [IMPLEMENTED: enforcement · PLANNED: scale]

- Every source directory must carry `SOURCE.json` with an allow-listed license. `load_source_dir` refuses anything else (tested).
- Each kept document is recorded in `manifest.json` with its SHA-256, source, license and path. This gives auditable lineage from weights back to data.
- **Data reality check.** Permissively licensed public Luau is small next to Python or JS. The exact size is unknown and is a Phase 0 measurement task. A useful model therefore needs (a) general permissive code and text (e.g., license-filtered datasets that publish provenance and honor opt-outs), (b) synthetic Luau (§4.2), and (c) documentation the Roblox license allows us to use. Licensing of Roblox Creator Docs for training must be confirmed before ingestion. It is not assumed.

### 4.2 Synthetic data factory [IMPLEMENTED v0]

`godmode/data/synthetic.py`: parameterized task families (reducers, validated remotes, leaderstats, and repair tasks). **Every sample is verified.** Solutions must be structure-valid and lint-clean. For repair tasks the broken input must *fail* verification, so the label carries real signal (tested).

Measured weakness: MinHash dedup removes ~48% of v0 templated samples as near-duplicates. The templates lack diversity. Next steps: model-generated samples through the same verifier gate, compositional task grammars, and mutation of real corpus code.

### 4.3 Filtering and dedup [IMPLEMENTED]

Quality filters cover length bounds, alphanumeric ratio, minified-line detection, Luau structural validity, and no `loadstring`. Dedup is exact (on comment- and whitespace-normalized text) plus MinHash near-dup (5-gram shingles, 64 permutations, Jaccard ≥ 0.85). The train/val split is by document content hash, so it is leakage-free and deterministic.

### 4.4 Training stages

| Stage | Status | Method |
|---|---|---|
| Pretraining | **[IMPLEMENTED, single process]** | Next-token CE, AdamW (β=0.9/0.95, wd on matrices only), warmup + cosine LR, grad clip, best/last checkpoints, JSONL logs |
| Fill-in-the-middle | [PLANNED] | PSM/SPM transforms at ~50% rate (Bavarian et al. 2022), so the model learns editor-style insertion |
| SFT | [PLANNED] | Chat template already used by synthetic data (`<|user|>…<|assistant|>`); loss masked to assistant tokens |
| RL | [PLANNED Phase 2] | GRPO-style group-relative policy optimization with **verifiable rewards**: structure, lint, type-check, unit tests, GRIB-style checks. Reward hacking guards: held-out verifiers, length-normalized rewards, human audit of top-reward samples |
| Continual learning | [PLANNED] | Replay buffer of prior-phase data, regression suite on every candidate, promotion gate (§12) |

### 4.5 Contamination control [IMPLEMENTED]

`contamination_report` computes 13-gram overlap between each benchmark reference and the training corpus. Tasks above 5% overlap are flagged and excluded from the headline metric `pass_rate_clean`. This already flagged 3–4 GRIB v0 tasks against our own seed corpus. The check works, and our own writing style leaks into benchmarks.

### 4.6 External models policy

Allowed: generating candidate synthetic data (always verifier-gated, with provenance `source=teacher:<model>`), and acting as GRIB competitors through the same adapter interface. Forbidden: serving them as GODMODE, and using their outputs where their terms prohibit training competing models. Terms are reviewed per provider before any use.

## 5. Project intelligence memory [PLANNED Phase 4]

Context windows are a cache, not memory. The design has four tiers:

1. **Working set.** Files under edit plus their direct dependencies, chosen per request.
2. **Symbol and dependency graph.** Built by the Luau front end: `require` edges, exported types, RemoteEvent producers and consumers (who `FireServer`s what, who handles it), CollectionService tags, and asset references. This graph detects architectural problems the model cannot see in a window: a remote with no server handler, a handler that skips validation (lint GM002 generalized across files), or a module cycle.
3. **Semantic index.** Embeddings from our own model over chunks (function-level), plus BM25 for exact identifiers. Hybrid retrieval is ranked by graph distance to the working set.
4. **Decision log.** Append-only records of design decisions and changes (who, why, which tests). Retrieved when touching the affected code.

Storage is SQLite for the graph and log, and a flat vector index first, so it runs locally. Distributed storage comes only if measurements demand it.

## 6. Distributed computing architecture [PLANNED Phase 9; design only]

Progressive stages. Each stage begins only when the previous one is saturated.

| Stage | Hardware | Parallelism |
|---|---|---|
| S0 (now) | 4-core CPU | none |
| S1 | 1 GPU | mixed precision (bf16), gradient accumulation, `torch.compile` |
| S2 | 1 node, 8 GPUs | FSDP (ZeRO-3-equivalent) data parallel |
| S3 | multi-node | FSDP + tensor parallel within node + pipeline across nodes; **expert parallel** (all-to-all dispatch) for MoE |
| S4 | separate clusters | pretraining / RL (rollout workers + learner) / eval & research / inference / asset generation |

Cross-cutting requirements:
- **Sharded, asynchronous checkpoints** with deterministic data-loader state, so resume is exact. Checkpoints are content-addressed with the manifest hash.
- **Fault tolerance.** Elastic launch (torchrun), automatic restart from the last checkpoint, and a node health-check before join.
- **Inference.** Continuous batching and paged KV cache on servers; quantized (int8/int4) local inference for the desktop app.
- **Scheduler.** Research jobs are preemptible; production retraining has reserved capacity.

## 7. Roblox Studio integration [PLANNED Phase 7]

Supported mechanisms only:

- **Studio plugin** (Luau) as the hands. It inspects and edits the DataModel, creates Instances, and sets properties. It wraps every change in `ChangeHistoryService` recordings so each AI change is undoable as one step.
- **Local bridge.** The plugin talks to the GODMODE desktop service over localhost HTTP (Studio plugins may use `HttpService` when enabled by the user). The protocol is a small typed RPC: `snapshot_tree`, `read_script`, `apply_patch(preview=true)`, `run_tests`, `get_output`.
- **File sync interop.** Rojo-compatible project layout, so code also lives in git.
- **Cloud execution.** Roblox Open Cloud APIs for headless Luau execution and publishing, where the user grants keys (publishing always requires approval, §10).
- **Honesty rule.** The agent reports a Studio operation as done only after the bridge returns its result. Previews are diffs, and nothing is applied silently.

## 8. Independent development environment [PLANNED Phase 7]

A desktop app (Tauri or Electron shell, web UI) with these workspaces:

| Workspace | Foundation |
|---|---|
| AI Workspace | Chat with plan/diff/verification panes; autonomy level selector (§10) |
| Code Studio | Monaco editor + open-source `luau-lsp` (MIT) for types and navigation; GODMODE completions via FIM |
| 3D Studio | three.js scene editor over a Roblox-compatible Instance model; import/export `.rbxlx` (XML place format) |
| UI Designer | Drag-and-drop over a ScreenGui model; exports Luau that constructs the hierarchy |
| Project Explorer | Backed by the §5 dependency graph |
| Asset / Testing / Research Labs | Asset pipeline; test runner + profiler; experiment registry browser |

## 9. Generative model families [THEORETICAL]

GODMODE-3D, -TEXTURE, -ANIMATE, -WORLD, -AUDIO, -UI, -SPATIAL and -OPTIMIZE are **proposals**. None exist. Each one needs its own data rights review, baseline, and evaluation before any build work. The most tractable first:

- **GODMODE-WORLD and -UI** generate *programs* (Luau that builds terrain or GUIs), not pixels or meshes. They reuse the language model and verifiers directly.
- **GODMODE-SPATIAL** trains on synthetic scene graphs (§3.5).

Mesh, texture, audio and animation generation need diffusion or flow models and licensed asset data. They are deferred until the language core is useful.

## 10. Autonomous agents and autonomy levels [PLANNED Phase 8]

Agents are roles played by the GODMODE model with different tools and system prompts, coordinated by a supervisor. They are not separate models.

- **Shared state.** Project memory (§5) plus a task board. Agents claim files through ownership locks. Edits are patches against a base revision, and conflicting patches are rejected and rebased.
- **Integration gate.** Every agent's patch goes through the verifier stack and project tests before merging.
- **Autonomy levels 1–5** (Assistant → Research Intelligence) map to permission sets. Destructive changes, publishing, credential access, external network actions and production model upgrades always require explicit human approval, at every level.

## 11. Evaluation and benchmarking

### 11.1 GRIB [IMPLEMENTED v0]

`benchmarks/grib/tasks_v0.jsonl` holds 14 tasks across 4 tiers (beginner → expert) and 9 categories. Every task ships a reference solution that must pass its own checks (tested). An empty answer must score 0 (tested; this caught a real gaming hole). Adapter crashes count as failures.

**v0 limitation:** grading is static (structure + security lint + regex assertions). It cannot verify runtime behavior. v1 adds execution-based grading: the Luau runtime plus a Roblox API mock layer (Players, RemoteEvent, DataStore with failure injection) and hidden unit tests per task.

### 11.2 Competitor protocol [PLANNED]

All systems run through the same `Adapter(prompt) -> str` interface, with identical prompts, tool access, sampling budget, and attempts. Reports include pass@1, `pass_rate_clean`, security failures, latency and cost. **No competitor scores are published until they are actually run.**

### 11.3 Metrics tracked

Pass rate (overall, clean, per tier and category), security failures, regression rate against the previous release, wall time, compute cost, and human interventions (agent tasks).

### 11.4 Experiment protocol

Same data, steps and hyperparameters; ≥3 seeds per arm; report mean ± std; register every run in `experiments/registry.jsonl` with git revision.

## 12. Continuous improvement engine [IMPLEMENTED: gate & registry · PLANNED: automation]

Loop: evaluate → find weaknesses (per-category GRIB failures, error clustering) → hypothesis → experiment → candidate → **promotion gate** → promote or record the negative result.

`promotion_gate` (tested) requires the primary metric to improve by more than the pooled seed std, with ≥2 seeds, and no regression on any guard metric (security failures, other benchmark slices, latency). It returns a decision and never swaps weights. Promotion is a separate, human-approved step that keeps the previous production version.

## 13. Security

- Generated code: lint GM001–GM007 today. Planned: type checking, taint analysis from `OnServerEvent` arguments to sinks (currency, damage, DataStore), and dependency scanning for Wally packages.
- Execution: model-generated code runs only in sandboxes (no network, resource limits). Never on a developer's live place without preview and approval.
- Model supply chain: checkpoints carry fingerprints. Planned: signed checkpoints and an append-only audit log of promotions.
- Refusals: the model must not produce exploits, executor scripts, anti-cheat bypasses or credential theft. A dedicated red-team slice in GRIB will measure this.
