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
| Product | GUI designer: builds real ScreenGui instance trees (menu, shop, inventory, settings, dialog, loading, timer, notification, HUD) from EN/FR/ES requests, no scripts | Implemented — **deterministic designer, not neural output; buttons are tagged (`GodmodeAction` attribute) but not wired** | `godmode/tools/gui.py`, `tests/test_gui.py` |
| Product | Multilingual requests (English, French, Spanish routing + FR/ES training prompts) | Implemented for EN/FR/ES only; other languages untested | `tests/test_multilingual.py` |
| Product | Model-first script intents (model writes the script, verified; tool fallback; `engine:"tool"` override) | Implemented | `tests/test_multilingual.py` |
| Product | Project memory, agents, IDE | Planned | — |
| Generative | GODMODE-3D/TEXTURE/ANIMATE/WORLD/AUDIO/SPATIAL/OPTIMIZE (neural) | Theoretical | — |

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

## QA round: request relevance (EN/FR/ES)

Measured with `rate.py`, 15 prompts through the real service and the `runs/sft-scripts/best.pt` checkpoint, ~19 s per run.

- **Bug found:** the verifier passed a model script as long as it parsed, linted and looked plausible, even when the script did something else. A French leaderstats request and a Spanish tween-door request shipped unrelated model scripts as "verified".
- **Fix:** a fourth check, `relevance:model_candidate` (`godmode/server/model_backend.py`), has two parts:
  - every concept the request names (speed, touch, colour, interval, door, spawn, coins, datastore, …; EN/FR/ES, accent-folded) needs matching API evidence in the code;
  - the topic in the model's header line (sword, spinning part, door, …) must be asked for by the request.
- **Result:**
  - FR leaderstats and ES tween door now fall back to the template tools (verified).
  - The three freeform prompts (speed boost, coin spawn, colour change) now return "no verified candidate" instead of a wrong script. Before the header check, a ClassicSword script and then a spinning-part script slipped through for the Spanish colour prompt.
- **FR/ES animation routing:** "fais une animation de danse" and "crea una animación de saltar" used to go to the model. They now reach the procedural animation tool (`ANIM_WORDS` in `godmode/tools/router.py`, FR/ES style keywords in `godmode/anim/styles.py`).
- **Limitation:** relevance is keyword- and header-based. It is necessary, not sufficient, and a script can still match on incidental APIs. On unseen freeform tasks the model mostly cannot write correct code; the system now says so.
- Tests: `tests/test_model_relevance.py`. Its negative gates were run: with the fix set aside, the tests fail. Full suite: 617 passed.

## QA round: plugin in a mock Studio

**Not performed:**
- No Roblox Studio is available in this environment (Linux container, no attached machine).
- Nothing below was tested in real Studio. That includes toolbar rendering, docking, theme visuals, real HTTP (only the mock `RequestAsync` was exercised) and real undo.

**What was performed:**
- The real plugin source (all 13 `plugin/src/*.luau` modules) is translated to Lua 5.4 by `godmode/luau/to_lua.py`.
- It is booted in an in-process Lua VM (`godmode/luau/lua54.py`, `PluginRuntime`) against `godmode/luau/roblox_mock.lua`.
- The mock is strict: unknown classes, properties and members raise errors. It is driven by an API table generated by `gen_roblox_api.py` → `roblox_api.lua`.
- The UI is driven by firing the same signals Studio would fire.

Tests are in `tests/test_plugin_boot.py` (24 tests) and `tests/test_plugin_runtime.py`.

| Check | Result in mock |
|---|---|
| Boot: one toolbar, one widget, hidden at start | pass, no runtime errors |
| Toolbar click toggles the widget and button state; 20 extra toggles | pass, connection count does not grow |
| Health check with no server | pass: notice shown, no crash |
| EN "build a house" offline → Apply | pass: 13 instances added in one `Commit` waypoint, input cleared |
| Undo / Redo of that build | pass: removes only the AI build and keeps a pre-existing user Part; redo restores it |
| Every applied change inside a ChangeHistory recording | pass (`unrecorded == 0`) |
| Discard | pass: nothing created, no waypoint |
| Empty or whitespace message | pass: not sent |
| Unload | pass: background `task.wait` loops stop |
| Server URL save: `[::1]`, `127.0.0.1`, `localhost` → no warning; `example.com` → "not on this computer" warning | pass |
| EN "make an idle animation" offline → apply both cards | pass: KeyframeSequence applied with a preview id registered, `PlayGodmodeIdle` script placed in `StarterPlayer.StarterCharacterScripts` |
| Apply clicked 3× on one card (2 in the same frame) | pass: one `GodmodeHouse`, one waypoint; with the card's state guard removed the test fails |
| Widget closed/reopened 5× while a card is pending, then Apply | pass: card still applies once, connection count does not grow |
| EN "make a main menu gui" offline → Apply → Undo | pass: one card; a real `ScreenGui` `StarterGui.MainMenuGui` (17 descendants, 3 TextButtons, 0 scripts) in one waypoint; Undo removes it |
| Server mode, `/chat` returns HTTP 500 · non-JSON 200 · JSON array 200 · connect failure | pass: "answering offline" notice, offline Apply card, nothing built before Apply, Send re-enabled, no runtime errors |
| Server reply with wrong field types (`reply_markdown` number, `actions` not a list) | pass: "(empty reply)", no card, no errors |
| Server slower than the 120 s client timeout, widget closed and reopened mid-request | pass: "timed out" notice, offline fallback card, Send re-enabled |
| Plugin unloaded while a `/chat` request is pending | pass: no errors, nothing built, no waypoint |
| Server reports a newer protocol version | pass: "too old" error, no `/chat` request sent |
| Second Send while a request is in flight | pass: one `/chat` request; with ChatUI's busy guard removed the test fails |

**Findings from this round, now fixed.** Each fix has a test that fails when the fix is reverted; this was checked.
- The plugin used the deprecated `Plugin:CreateDockWidgetPluginGui`. It now uses `CreateDockWidgetPluginGuiAsync` (`test_boot_is_clean_…`).
- `RegisterKeyframeSequence` was called on the wrong service. It is now called on `KeyframeSequenceProvider` (`test_offline_animation_apply_registers_a_preview_id`).
- `isLocalUrl` did not match `[::1]` hosts. It does now (`test_saving_a_server_url_warns_only_for_remote_hosts`).
- A server body that was a JSON array (e.g. `[1,2,3]`) was accepted as a protocol object, so the plugin showed an empty reply instead of falling back offline. `HttpClient` now rejects it (`test_server_failure_on_chat_falls_back_offline_without_crashing`).

**Mock fidelity fixes:**
- `StarterPlayer` now gets its default `StarterPlayerScripts` and `StarterCharacterScripts` children, as in a new Studio place.
- Added a `KeyframeSequenceProvider` mock that type-checks its argument.
