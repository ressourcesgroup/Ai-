# PROJECT GODMODE

An independent, from-scratch AI system for Roblox development. It has its own tokenizer, model, weights, data pipeline, verifiers, benchmark, and a **Roblox Studio plugin** you can chat with. It is not a wrapper around any external AI.

> **Honest status:** the whole system works end to end and has 460+ passing tests.
>
> - **The plugin is reliable for the things its tools cover:** building obbies, houses and arenas, writing validated remotes and data saving, and creating 15 animation styles.
> - **For questions, it shows cited examples from real Roblox code.**
> - **Our own neural model is small** (12.6M parameters, trained on CPU on Roblox's licensed Luau corpus). It writes Roblox-flavored code that is usually **not correct yet**. The plugin only shows model code that passes our checks, and labels it.
>
> Making the model genuinely good needs GPU training at larger scale. See [docs/TRAINING_CLOUD.md](docs/TRAINING_CLOUD.md). [docs/STATUS.md](docs/STATUS.md) lists exactly what is tested, experimental or planned.

## Use the Studio plugin (no setup needed)

1. **Copy** `plugin/dist/GodmodePlugin.rbxmx` into your Roblox Plugins folder:
   - Windows: `%LOCALAPPDATA%\Roblox\Plugins`
   - macOS: `~/Documents/Roblox/Plugins`

   Or open Studio > Plugins tab > *Plugins Folder*.
2. **Restart Studio**, open any place, and click **GODMODE** in the Plugins toolbar.
3. **Ask it things.** For example: "build an obby", "make a sprint animation", "make an aw shucks emote", "save player data", "add a leaderboard with cash".

The plugin works **offline by default**. It holds 29 pre-verified builds, scripts and animations (14 tools + 15 R15 animation styles) and picks one by keywords.

**Optional server.** It adds custom sizes, counts and colors, answers to questions, code search, and our neural model. To use it:
1. Run `pip install torch numpy && pip install -e .`
2. Run `python -m godmode.server --ckpt runs/small-full2/best.pt`
3. Type `/server on` in the plugin chat, and allow localhost HTTP when Studio asks.

`/server off` returns to offline mode.

Every change is shown as a **preview card first**. Nothing happens until you click **Apply**, each change is a single **Ctrl+Z** undo step, and deletes need a second confirmation. The *autonomy level* (1 Assistant … 5 Research) controls whether it only answers (level 1) or also proposes changes. Every reply says where it came from: `tool:…`, `retrieval`, or `model:…`.

See [plugin/README.md](plugin/README.md) for details. **The plugin has been structurally verified but never run inside Roblox Studio** (impossible in our build environment), so please report anything that breaks.

## Animations

There are 15 procedural R15 styles: idle, walk, **run (chase/"Fugitive" style)**, jump, **punch combo**, **kick**, block, **hit reaction**, **knockdown (elimination style)**, victory, wave, **"aw shucks"**, dance, sit, crouch. They are created as `KeyframeSequence`s that you can open and refine in the Animation Editor. They were designed from the titles of the reference videos, not from the videos themselves (we can't watch video) and not from motion capture.

## What's inside

| Path | What |
|---|---|
| `plugin/` | Roblox Studio plugin (Luau) + `build_rbxmx.py` packager |
| `godmode/server` | Local HTTP server the plugin talks to: router, tools, search, model fallback |
| `godmode/tools` | Verified generators: builders, remotes, leaderstats, DataStores, UI, NPCs, doors |
| `godmode/anim` | Procedural R15 animation engine |
| `godmode/model`, `godmode/tokenizer` | Our transformer (GQA, RoPE, SwiGLU, MoE, recurrent depth) and BPE tokenizer, written from scratch |
| `godmode/train` | Training: single GPU/CPU, multi-GPU (DDP), bf16, grad accumulation, continue-from-checkpoint |
| `godmode/data` | License-gated ingestion, dedup (MinHash + LSH), leakage-free clustered splits, synthetic data |
| `godmode/luau` | Luau lexer, structure checker, Roblox security lint |
| `godmode/eval`, `benchmarks/grib` | GRIB benchmark (21 tasks) with contamination control |
| `godmode/research` | Experiment registry + statistical promotion gate |
| `docs/` | [Architecture](docs/ARCHITECTURE.md) · [Status](docs/STATUS.md) · [Model card](docs/MODEL_CARD.md) · [Plugin protocol](docs/PLUGIN_PROTOCOL.md) · [Cloud training](docs/TRAINING_CLOUD.md) · [Roadmap](docs/ROADMAP.md) · [Backlog](docs/BACKLOG.md) |

## Train it yourself

```bash
python -m godmode fetch-data roblox-luau-corpus     # Roblox's MIT-licensed Luau dataset
python -m godmode build-data --vocab-size 8192 --out runs/data-full
python -m godmode train --data runs/data-full --config configs/small-cpu.json --steps 3000 --out runs/small
python -m godmode grib --adapter ckpt:runs/small/best.pt   # model alone
python -m godmode grib --adapter system                    # whole plugin pipeline
pytest -q
```

## Data and licenses

- Our code: MIT.
- Training data:
  - Our own seed corpus (MIT).
  - [Roblox/luau_corpus](https://huggingface.co/datasets/Roblox/luau_corpus) (MIT, © Roblox Corporation; built from experiences whose creators opted in).
  - Verified synthetic examples.
- Every document's source and license is recorded in the build manifest.
