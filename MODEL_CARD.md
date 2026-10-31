# GodmodeLM — model card (script SFT, `runs/sft-scripts/best.pt`)

**What it is:** a 12.6M-parameter decoder-only transformer (RMSNorm, RoPE, GQA, SwiGLU; BPE vocab 8192, context 768)
trained from scratch on this project's licensed/seed corpus, then supervised-fine-tuned to answer
`<|user|>request` with `<|assistant|>` a **complete Roblox Luau script** whose first line says where it goes
(e.g. `-- Script in ServerScriptService: ...`).

**Training data (SFT):** 3,807 programmatically generated, verifier-checked prompt→script pairs across 35 script
families (kill brick, coin pickup, teleporter, sprint, day/night, leaderstats, katana tool, ...). 32 families trained;
`speed_pad`, `name_tag`, `camera_shake` held out. Loss on assistant tokens only. 1500 steps, ~60 min on 4 CPU cores.

**How it's used:** the server samples N candidates (N set by the thinking level: low 1, medium 4, high 8, extra high 16,
max 32), runs each through the Luau structure checker + security lint, re-ranks at high and above, and returns the best
verified script. All model-written code is marked `requires_approval`; the plugin shows it before inserting. Without a
valid placement header the code is inserted as a disabled ModuleScript draft in ServerStorage.

## Results (honest)

See `docs/STATUS.md` → "Measured results (milestone M2)". Summary:

* Seen task types, fresh prompts: **81% complete at low, 94% at medium** (pretrain-only model: 0%).
* **Unseen task types: 11–22% complete.** It usually returns a working-looking script for a *different* task.
* GRIB-v0-static: 1/21 for the model alone, 4/21 for the full system.

## Limitations

* Small model, narrow data: it is good at ~32 kinds of script and weak outside them. It does not "answer any question".
* Checks are static (our heuristic Luau checker, not the official compiler, and no in-game playtests). Code that passes
  may still have runtime bugs — review before use.
* Offline mode (no server) uses keyword routing + built-in tools, not this model.
* No comparison against frontier models has been run.
