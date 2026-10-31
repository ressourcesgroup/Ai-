# GODMODE Roblox Studio plugin

A dock-widget chat panel for Roblox Studio that talks to the local GODMODE server
(`python -m godmode.server`) over HTTP JSON, shows what the server proposes, and
applies it **only when you click Apply**. The wire contract is
[`docs/PLUGIN_PROTOCOL.md`](../docs/PLUGIN_PROTOCOL.md) (protocol v1).

```
plugin/
  src/                 Luau sources (--!strict)
    Main.luau            entry Script: toolbar button, dock widget, settings, request flow
    ChatUI.luau          header, message list, thinking blocks, composer, empty state, settings view
    Markdown.luau        markdown-lite: headings, fenced code panels, **bold**, *italic*, `code`, lists
    OfflineEngine.luau   serverless replies from OfflineData.luau (pre-verified builds)
    HttpClient.luau      HttpService:RequestAsync in pcall, timeout, friendly errors
    Context.luau         Selection:Get() + StudioService.ActiveScript -> request context
    ActionPreview.luau   one card per proposed action (preview, diff, checks badge, Apply/Discard)
    ActionApplier.luau   applies actions, one ChangeHistoryService recording per action
    PropertyDecoder.luau JSON property encoding -> Roblox values (allow-listed types only)
    PathResolver.luau    dot paths <-> instances, restricted to normal game services
    Theme.luau, Ui.luau  Studio theme colors (light/dark, live ThemeChanged) and UI helpers
  build_rbxmx.py       packs src/ into dist/GodmodePlugin.rbxmx (Python stdlib only)
  dist/GodmodePlugin.rbxmx
```

## Install

1. Build (optional, a built copy is committed): `python3 plugin/build_rbxmx.py`
2. Copy `plugin/dist/GodmodePlugin.rbxmx` into your Studio **Plugins folder**:
   * Windows: `%LOCALAPPDATA%\Roblox\Plugins`
   * macOS: `~/Documents/Roblox/Plugins`
   * or in Studio: **Plugins** tab > **Plugins Folder**, and drop the file there.
3. Restart Studio (or reload plugins). A **GODMODE** toolbar appears on the Plugins tab;
   its button toggles the GODMODE panel (docked on the right by default).

## Start the server

```bash
python -m godmode.server                 # http://127.0.0.1:8765
python -m godmode.server --token SECRET  # optional shared secret
```

Open **Settings** (the gear in the header):

* **Mode** - *Offline* (default; built-in pre-verified builds, no HTTP at all) or
  *Online* (every message goes to `<Server URL>/chat`; if the server cannot be reached
  the reply comes from the offline engine and a notice says so). The chat command
  `/server on` / `/server off` still works and flips the same setting.
* **Server URL** - default `http://127.0.0.1:8765`; `https://` URLs are accepted.
  All requests (`/health`, `/chat`, `/verify`) use this base URL.
* **Access token** - only if you started the server with `--token` (sent as the
  `X-Godmode-Token` header).
* **Test connection** calls `GET <url>/health` and shows the server version, protocol
  and whether a model is loaded. **Save** stores the URL and token.

Mode, URL, token, autonomy and thinking level are stored with `plugin:SetSetting` on
this computer (the token in plain text in Studio's plugin settings; use a throwaway
local token). The plugin warns you if you point it at a non-local URL.

## The panel

```
+-------------------------------------------+
| GODMODE  (o Online · 127.0.0.1:8765) [⚙][+]|
|-------------------------------------------|
|                   [ make a leaderboard ]  |  you (right-aligned bubble)
| ▸ Thought for 0.3s · 5 steps · Medium     |  collapsed; click to see steps
| ## Leaderboard                            |  assistant reply, full width
| Adds `leaderstats` with **Coins** ...     |
| +-- luau ------------- Select  Insert --+ |  dark code panel
| +---------------------------------------+ |
| (tool:leaderboard) (✓ 3/3 checks)         |  source + verification chips
| +- CREATE_SCRIPT  Leaderboard ----------+ |  action card
| | ✓ 3/3 checks ▸   [Apply] [Discard]    | |
|-------------------------------------------|
| Ask GODMODE...                            |  grows to ~6 lines
| [Thinking: Medium ▾] [A2 ▾]       [Send]  |
+-------------------------------------------+
```

* **+** starts a new chat (clears messages and history). An empty chat shows four
  starter prompts.
* **Enter** sends, **Shift+Enter** inserts a newline.
* **Thinking** picks Low / Medium / High / Extra high / Max (sent as `thinking`;
  default Medium). Offline, the level is echoed but changes nothing.
* **A1-A5** is the autonomy level (see below).
* **Select** on a code panel selects the text so Ctrl+C copies it (plugins cannot
  write the clipboard). **Insert** creates `ServerScriptService.GodmodeSnippet` from the
  block through the normal apply path (one undo step).
* Destructive cards are outlined red and need **Delete...** then **Confirm delete**.

## Allow HTTP

The first request makes Studio ask whether the GODMODE plugin may send HTTP requests
to `127.0.0.1`. Choose **Allow**. If you dismissed the prompt, open
**Plugins > Manage Plugins**, find GODMODE and allow its HTTP access. The protocol
also asks for **Game Settings > Security > Allow HTTP Requests** to be on. When a
request is blocked the panel shows these steps instead of a raw error. In Online mode
the header chip shows the last `/health` result (green `Online · host`, red
`Unreachable`; click it to re-check). It is polled every 30 s while the panel is open.
Offline mode makes no HTTP requests, so no prompt appears.

## Usage examples

* *"build an obby with 10 red platforms"* - expect a `create_instances` card (model
  outline) and possibly a `create_script` card (kill bricks); click **Apply** on each,
  in order.
* *"make a sprint animation"* - a `create_animation` card; applying builds a
  `KeyframeSequence` (default `ServerStorage.GodmodeAnimations` if the action names no
  parent), selects it, and reports a local preview id from
  `AnimationClipProvider:RegisterKeyframeSequence` (session-local, not an uploaded asset).
* *"create a validated remote event for buying items"* - script cards with a source
  preview and the server's structure/lint verification; **Re-verify** calls `/verify`.
* *"what does task.defer do?"* - a text answer only. Every reply shows its `source`
  as a chip under the reply (`tool:...`, `retrieval`, `model:...`, `verifier`,
  `router`, `none`, `offline:...`).

Selected instances (paths, class names) and the active script's source are sent as
context, so "review my open script" or "delete the selected part" work.

## Autonomy levels and approval

| level | name | what happens |
|------|------|--------------|
| 1 | Assistant | Text only; the server returns no actions. |
| 2 | Copilot | Actions are proposed; every one needs your **Apply** click. |
| 3 | Engineer | Same approval as 2. |
| 4 | Autonomous | Non-destructive actions marked `requires_approval: false` can be applied together with one **Apply N safe actions** click (in order; stops at the first failure). |
| 5 | Research | Same approval rules as 4. |

At every level:

* Nothing is ever applied without a click. There is no silent auto-apply.
* Destructive actions (`delete_instance`, or anything marked `destructive`) have a red
  card, are never part of a batch, and need **Delete...** then **Confirm delete**.
* Each applied action is **one undo step** (`ChangeHistoryService` recording). If an
  action errors, the recording is cancelled, Studio reverts its partial changes, and
  the chat says *Did NOT apply ...* with the exact error. Success is only reported
  after the operation actually completed.
* Created instances are selected after applying.

## Protocol additions

These extend protocol v1 in a backward-compatible way (all fields optional; the
authoritative contract is still `docs/PLUGIN_PROTOCOL.md`):

* **`POST /chat` request** gains `thinking`: one of `"low"`, `"medium"`, `"high"`,
  `"xhigh"`, `"max"` (default `"medium"`). Sent in both modes; the offline engine
  receives it too.
* **`POST /chat` response** may include
  `thinking: {level, summary, elapsed_ms, steps: [{label, detail, ms}]}`. When present
  the panel renders a collapsed "Thought for Xs · N steps" block; when absent, no block.
  The offline engine always returns one describing what it actually did (read request,
  keyword scores, which pre-verified build it picked) and says the level has no effect.
* **`GET /health`** may include `thinking_levels: [{id, label, description}]`. Known
  ids update the picker's labels and tooltips; unknown ids are ignored. If absent the
  built-in descriptions are used.

## Security model

* The plugin never executes code from the server: no dynamic code loading
  (the source contains none of the dynamic-execution globals; a test checks this).
  Scripts are created as text; you decide whether to run your game.
* Instances are built declaratively. `PropertyDecoder` accepts only number, string,
  bool, Vector3, Vector2, CFrame, Color3, UDim2, UDim, Enum, BrickColor and
  NumberRange; `Parent` and `Name` cannot be set through properties.
* Paths must start at an allow-listed game service (Workspace, ReplicatedStorage,
  ReplicatedFirst, ServerScriptService, ServerStorage, StarterGui, StarterPack,
  StarterPlayer, Lighting, SoundService, Teams, TextChatService, MaterialService).
  CoreGui, CorePackages, PluginGuiService and other internals are refused, and a
  service itself cannot be deleted.
* Sizes are bounded (tree nodes, depth, source length, message length).

## What is and isn't tested

**The plugin has NOT been run inside Roblox Studio.** Our build environment is Linux
without Studio, so it is *structurally verified only*. `tests/test_plugin.py` checks:

* every `src/*.luau` file starts with `--!strict`, passes
  `godmode.luau.check_structure` (lexing + block/bracket balance) and has zero
  `godmode.luau.lint` findings;
* `build_rbxmx.py` output parses with `xml.etree`, has one `Script` named
  `GodmodePlugin` with every other module as a `ModuleScript` child, unique `RBX`
  referents, sources that round-trip byte-for-byte (including a `]]>` CDATA-splitting
  case and CRLF normalisation), and the committed `dist/` file is up to date;
* every `require(script.X)` / `require(script.Parent.X)` names a packaged module;
* no source contains `loadstring`, `getfenv` or `setfenv`;
* the property decoder handles every `$type` in `docs/PLUGIN_PROTOCOL.md` plus `$enum`;
  `ActionApplier` has a handler for every action section in the protocol; undo
  recording commits on success and cancels on error; pose CFrames use
  `CFrame.Angles` (X, Y, Z) and property CFrames use `CFrame.fromOrientation`, as
  documented; the token header matches the protocol; the path allow-list excludes
  Studio internals.

**Not verified:** Luau type-checking (`--!strict` analysis), runtime behaviour of any
Roblox API call, the UI layout and theming (including bubble wrapping, the auto-growing
composer, Enter/Shift+Enter detection and smooth scrolling), the Studio HTTP permission prompt text,
`ScriptEditorService` behaviour with open/Team Create scripts, and how the built
animations actually look. Please report issues found in Studio.
