# GODMODE Plugin Protocol (v1)

The contract between the GODMODE local server (`python -m godmode.server`, code in
`godmode/server/`) and the Roblox Studio plugin (`plugin/`). This document is
**append-only**: fields may be added in later versions, existing fields keep
their meaning. Clients must ignore unknown fields.

## Transport

* HTTP/1.1, JSON bodies (`Content-Type: application/json; charset=utf-8`), UTF-8.
* Default address `http://127.0.0.1:8765`. The server binds **127.0.0.1 only**
  unless explicitly started with `--host`.
* Optional shared secret: if the server was started with `--token T` (or env
  `GODMODE_TOKEN`), every request must carry the header `X-Godmode-Token: T`,
  otherwise the server answers `401`. Studio's `HttpService:RequestAsync` can set it.
* Request bodies larger than 1 MiB are rejected with `413`.
* Every error is JSON: `{"ok": false, "error": {"code": "<string>", "message": "<string>"}}`
  with HTTP status 400 (`bad_json`, `bad_request`), 401 (`unauthorized`),
  404 (`not_found`), 405 (`method_not_allowed`), 413 (`too_large`), 500 (`internal`).
* Studio requirement: *Game Settings > Security > Allow HTTP Requests* must be on.

## Honesty rules (hard requirements)

1. Every `/chat` reply names the component that produced it in `source`:
   * `tool:<name>` - a deterministic, verified template generator (e.g. `tool:obby`). Not AI output.
   * `retrieval` - snippets retrieved (BM25) from the licensed corpus, cited with source + license.
   * `model:<checkpoint>` - text sampled from the GODMODE neural model at that checkpoint path.
   * `verifier` - output of the structural checker + linter (`godmode.luau`).
   * `router` - fixed help / clarification text from the intent router.
   * `none` - nothing could answer (e.g. no checkpoint loaded); the reply says so.
   `reply_markdown` also begins with a line stating the source in plain words.
2. The server **never** changes Studio and never claims a change happened. It
   only *proposes* `actions`. The plugin applies them, after user approval, and
   is the only party that may report "applied".
3. Retrieval results and templates are never presented as the model's output.

## Endpoints

### `GET /health`

```json
{
  "ok": true,
  "version": "0.1.0",
  "protocol_version": 1,
  "model": {"loaded": false, "checkpoint": null, "detail": "no checkpoint configured"},
  "components": {
    "router": "keyword/regex intent classifier (not the neural model)",
    "tools": ["remote_event", "leaderstats", "..."],
    "animation_styles": ["idle", "walk", "..."],
    "retrieval": {"ready": false, "documents": 0, "detail": "index is built lazily on first question"},
    "model": "model:<checkpoint>"
  }
}
```

### `POST /chat`

Request:

```json
{
  "message": "make an obby with 10 red platforms",
  "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
  "context": {
    "selection": [{"path": "Workspace.Map.Door", "className": "Model", "name": "Door"}],
    "openScript": {"path": "ServerScriptService.Combat", "source": "-- luau source"}
  },
  "autonomy": 3
}
```

* `message` (string, required, max 8000 chars).
* `history` optional, informational only (the router is stateless).
* `context` optional. `selection` paths and `openScript` are used by some tools
  (delete selected, review/fix open script).
* `autonomy` integer 1-5, default 2.
* `thinking` optional: `low` | `medium` (default) | `high` | `xhigh` | `max` (labels such as
  `"Extra high"` are accepted). Unknown values answer `400 bad_request`. See *Thinking levels* below.

Response:

```json
{
  "ok": true,
  "reply_markdown": "**Source:** tool:obby (deterministic template, verified) ...",
  "source": "tool:obby",
  "intent": {"name": "build_obby", "confidence": 0.9, "router": "keyword-regex", "params": {"count": 10, "color": "red"}},
  "actions": [Action, ...],
  "verification": [{"check": "structure:ServerScriptService.GodmodeKillBricks", "ok": true, "detail": "..."}],
  "warnings": ["..."],
  "autonomy": 3,
  "applied": false
}
```

* `applied` is always `false`: the server never applies anything.
* `verification` lists every check run on generated Luau (`structure:<label>`,
  `lint:<label>`) and on animation payloads (`animation:<name>`). If a generator's
  output fails verification, `actions` is empty, the reply explains the error and
  `verification` shows the failing checks.

### `POST /verify`

Request `{"source": "<luau>"}`. Response:

```json
{
  "ok": true,
  "source": "verifier",
  "structure": {"ok": true, "errors": []},
  "lint": [{"rule": "GM006", "severity": "warning", "line": 3, "message": "..."}],
  "passed": false,
  "verification": [{"check": "structure", "ok": true, "detail": "..."}, {"check": "lint", "ok": false, "detail": "1 finding(s)"}]
}
```

`passed` is true when the structure check passes and there are no lint findings.
The structural checker is heuristic ("plausible", not "compiles").

## Autonomy levels

| level | server behaviour |
|-------|------------------|
| 1 | text only: `actions` is always `[]`; code is shown in `reply_markdown`. |
| 2-3 | actions returned; every action has `requires_approval: true`. |
| 4-5 | actions returned; non-destructive actions have `requires_approval: false` (the plugin *may* batch-apply them after one confirmation); destructive actions are **always** `requires_approval: true`. |

## Actions

Actions are ordered; the plugin must apply them in array order (later actions may
use instances created by earlier ones). Common fields on every action:

```json
{"id": "a1", "type": "<type>", "description": "human readable", "destructive": false, "requires_approval": true}
```

Paths are dot paths from the DataModel root starting at a service name, e.g.
`ServerScriptService.Combat`, `Workspace.GodmodeObby`, `StarterPlayer.StarterPlayerScripts`.
`Workspace` means `game.Workspace`. Generated names never contain dots. The plugin
resolves each segment with `FindFirstChild` (first segment: `game:GetService`).
A missing `parent_path` is an error for that action (plugin reports, does not guess).

### `create_script`

```json
{"type": "create_script", "parent_path": "ServerScriptService", "name": "GodmodeKillBricks",
 "class": "Script", "source": "-- luau"}
```

`class` is `Script` | `LocalScript` | `ModuleScript`. If a child with the same name
already exists the plugin should ask (or append a suffix); the server does not assume.

### `edit_script`

```json
{"type": "edit_script", "path": "ServerScriptService.Combat", "new_source": "-- full new source"}
```

Replaces the whole `Source`. The plugin should show a diff and record an undo waypoint.

### `create_instances`

```json
{"type": "create_instances", "parent_path": "Workspace",
 "tree": {"className": "Model", "name": "GodmodeHouse", "properties": {}, "attributes": {}, "tags": [],
          "children": [{"className": "Part", "name": "Floor", "properties": {"Anchored": true}, "children": []}]}}
```

Node fields: `className` (required), `name` (required), `properties` (object),
`children` (array), optional `attributes` (name -> number/string/bool, applied via
`SetAttribute`) and optional `tags` (array of strings, applied via `CollectionService:AddTag`).
Set properties in object key order, but always set `Parent` last (the plugin does
that itself). `Size` is listed before `CFrame` by the server.

#### Property value encoding

| Roblox type | JSON | Luau construction |
|-------------|------|-------------------|
| number / string / bool | raw JSON value | as is |
| Vector3 | `{"$type": "Vector3", "value": [x, y, z]}` | `Vector3.new(x, y, z)` |
| Vector2 | `{"$type": "Vector2", "value": [x, y]}` | `Vector2.new(x, y)` |
| CFrame | `{"$type": "CFrame", "position": [x, y, z], "rotation": [rx, ry, rz]}` (degrees) | `CFrame.new(x, y, z) * CFrame.fromOrientation(math.rad(rx), math.rad(ry), math.rad(rz))` (same convention as the `Orientation` property: Y, then X, then Z) |
| Color3 | `{"$type": "Color3", "rgb": [r, g, b]}` (0-255 integers) | `Color3.fromRGB(r, g, b)` |
| UDim2 | `{"$type": "UDim2", "value": [xScale, xOffset, yScale, yOffset]}` | `UDim2.new(...)` |
| UDim | `{"$type": "UDim", "value": [scale, offset]}` | `UDim.new(scale, offset)` |
| Enum | `{"$enum": "Material.Neon"}` | `Enum.Material.Neon` |
| BrickColor | `{"$type": "BrickColor", "name": "Bright red"}` | `BrickColor.new("Bright red")` |
| NumberRange | `{"$type": "NumberRange", "value": [min, max]}` | `NumberRange.new(min, max)` |

Instance-reference properties (e.g. `PrimaryPart`, `Part0`) are never sent.

### `create_animation`

```json
{"type": "create_animation", "name": "GodmodeWalk", "parent_path": "ReplicatedStorage.GodmodeAnimations",
 "rig": "R15", "looped": true, "priority": "Movement", "duration": 1.0,
 "hierarchy": {"HumanoidRootPart": null, "LowerTorso": "HumanoidRootPart", "UpperTorso": "LowerTorso", "...": "..."},
 "joints": {"LowerTorso": "Root", "UpperTorso": "Waist", "Head": "Neck", "...": "..."},
 "keyframes": [
   {"time": 0.0, "name": "contact", "easing_style": "Cubic", "easing_direction": "InOut",
    "poses": {"LowerTorso": {"rotation": [0, 0, 0], "position": [0, 0, 0]},
              "LeftUpperLeg": {"rotation": [25, 0, 0], "position": [0, 0, 0]}}}
 ],
 "markers": [{"time": 0.2, "name": "Hit", "value": "jab"}],
 "style": "walk", "params": {"speed": 1.0, "intensity": 1.0},
 "provenance": "procedural (designed from reference video titles, not motion capture)"}
```

The plugin builds a `KeyframeSequence` (`Loop = looped`, `Priority = Enum.AnimationPriority[priority]`)
under `parent_path` (creating missing Folders on that path is allowed for this action only),
with one `Keyframe` per entry (`Time = time`, `Name = name`). Inside each Keyframe it
builds nested `Pose` instances following `hierarchy` (part -> parent part; `null` = root).
Pose instances are named after the **part** (R15 KeyframeSequence convention). Every
ancestor of a posed part must exist as a Pose (the root `HumanoidRootPart` pose is
always identity). Each pose's `CFrame = CFrame.new(px, py, pz) * CFrame.Angles(math.rad(rx), math.rad(ry), math.rad(rz))`
(X, then Y, then Z) in the joint's (Motor6D) space. Weights: every part listed in a keyframe's
`poses` gets `Weight = 1`; filler ancestor poses created only to complete the nesting (always
`HumanoidRootPart`, plus any ancestor a keyframe omits) get identity `CFrame` and `Weight = 0`.
The server currently lists all 15 posed parts in every keyframe.
`EasingStyle = Enum.PoseEasingStyle[easing_style]`, `EasingDirection = Enum.PoseEasingDirection[easing_direction]`
(a pose may override both with its own `easing_style` / `easing_direction`).
`easing_style` is one of `Linear, Constant, Cubic, Elastic, Bounce, CubicV2`;
`easing_direction` is one of `In, Out, InOut`. `markers` (optional) become
`KeyframeMarker`s on the keyframe at or just before `time`.
`joints` maps part -> Motor6D name for display only.

Axis convention (joint space): +X right, +Y up, -Z forward. Positive X rotation
swings a limb forward/up (shoulder, hip), positive elbow X flexes the forearm,
negative knee X bends the knee, negative waist/neck X leans/nods forward.

### `delete_instance`

```json
{"type": "delete_instance", "path": "Workspace.OldMap", "destructive": true, "requires_approval": true}
```

Always destructive, always `requires_approval: true`, at every autonomy level.
The plugin should use `ChangeHistoryService` so the user can undo.

## Versioning

`protocol_version` is `1`. Additions are backwards compatible; a breaking change
would bump it and the plugin should refuse servers with a newer major version.

## Additional response fields (added in v1, optional)

* `/chat` `citations` (retrieval replies only): `[{"score", "source", "license", "path", "snippet"}]`.
* Action field `id` (`"a1"`, `"a2"`, ...) is unique within one response; use it in UI and logs.
* `create_animation` fields `meta` (`{"clamped": <int>, "description": <string>}`) and `destructive: false`.

## How replies are produced (server internals, informative)

1. **Intent router** (`godmode/tools/router.py`): a keyword/regex rule table with summed
   weights and a capped confidence score in `intent.confidence`. It is **not** the neural
   model. A tool intent needs confidence >= 0.5. Questions (how/what/why...) go to retrieval,
   other code requests to the model, everything else gets a router clarification.
2. **Tools** (`godmode/tools/`): deterministic generators. Every generated Luau source is
   checked with `godmode.luau.check_structure` and `godmode.luau.lint` at runtime; any failure
   returns an error and no code. Tools: `remote_event`, `leaderstats`, `datastore`, `ui_hud`,
   `npc_patrol`, `tween_door`, `moving_platform`, `house`, `obby`, `tower`, `staircase`,
   `arena`, `tree`, `car`, `animation`, `delete_selected`, `script_review`.
   Parameters parsed from the message: counts ("10 platforms", "5 floors", "20 steps"),
   `size N`, `width N`, `height N`, `N studs apart` / `spacing N`, colour names, material
   names, `called X`, tempo (fast/slow) and intensity (subtle/exaggerated). Counts are clamped.
3. **Retrieval** (`godmode/server/retrieval.py`): BM25 over the MIT-licensed corpora, built
   lazily and cached under `runs/retrieval/`. Always labelled as retrieved examples.
4. **Model** (`godmode/server/model_backend.py`): best-of-N sampling (`--best-of`, default 4)
   from `--ckpt` / `GODMODE_CKPT`, prompt style `comment` (default: the request as `-- ` comment
   lines, matching the training documents) or `chat` (`<|user|>...\n<|assistant|>`).
   Candidates must pass structure + lint + a plausibility filter; otherwise the reply says the
   model could not produce verified code. The prompt style defaults to `auto`: `chat` for an
   instruction-tuned (SFT) checkpoint, `comment` otherwise. If the code starts with a placement
   header (`-- Script in ServerScriptService: ...`) for a sensible class/container pair, it is
   proposed there; otherwise as an inert `ModuleScript` draft in `ServerStorage`. Model code
   always has `requires_approval: true`. Model requests on CPU can take tens of seconds;
   the plugin should use a generous HTTP timeout and show a "thinking" state.

## Animation styles

Styles (`godmode/anim/`): `idle` (breathing, loop), `walk` (loop), `run` (sprint / chase,
"Fugitive"-style lean and arm pump, loop), `jump`, `punch` (jab-cross-hook with wind-ups,
`Hit` markers), `kick`, `block` (guard hold, loop), `hit` (stagger), `knockdown`
(Squid-Game-inspired "elimination" collapse, non-gory, ends lying down), `victory`, `wave`
(loop), `shucks` ("aw shucks": head tilt, hand to back of neck, foot scuff, shrug), `dance`
(loop), `sit` (loop), `crouch` (crouch-sneak, loop). Looped styles start and end on the
identical pose. `speed` (0.25-3) divides all times; `intensity` (0-1.5) scales every angle
and offset, after which joint limits are enforced by clamping.

**Provenance:** these animations were designed by hand from the reference video **titles**
the user supplied. The videos were not watched and nothing is motion capture.

Joint rotation limits (degrees, joint space as defined above):

| part (joint) | X | Y | Z |
|---|---|---|---|
| LowerTorso (Root) | -95..95 | -180..180 | -95..95 |
| UpperTorso (Waist) | -45..30 | -60..60 | -35..35 |
| Head (Neck) | -50..40 | -75..75 | -35..35 |
| LeftUpperArm (LeftShoulder) | -60..180 | -90..90 | -180..30 |
| LeftLowerArm (LeftElbow) | 0..150 | -20..20 | -20..20 |
| LeftHand (LeftWrist) | -70..70 | -30..30 | -45..45 |
| RightUpperArm (RightShoulder) | -60..180 | -90..90 | -30..180 |
| RightLowerArm (RightElbow) | 0..150 | -20..20 | -20..20 |
| RightHand (RightWrist) | -70..70 | -30..30 | -45..45 |
| LeftUpperLeg (LeftHip) | -45..120 | -45..45 | -60..20 |
| LeftLowerLeg (LeftKnee) | -150..0 | -5..5 | -5..5 |
| LeftFoot (LeftAnkle) | -50..35 | -20..20 | -20..20 |
| RightUpperLeg (RightHip) | -45..120 | -45..45 | -20..60 |
| RightLowerLeg (RightKnee) | -150..0 | -5..5 | -5..5 |
| RightFoot (RightAnkle) | -50..35 | -20..20 | -20..20 |

Position offsets: LowerTorso X/Z within +-4 studs, Y within -3..2 studs (bob, crouch, fall);
every other part within +-0.5 studs. These limits are hand-chosen plausible ranges, not
anatomical data. Each animation is accompanied by a verified LocalScript
(`StarterPlayer.StarterCharacterScripts.Play<Name>`) that loads it through the Humanoid's
`Animator`, toggles it with **G**, and destroys the track / unbinds input on death or removal.
In Studio it can play the unpublished KeyframeSequence via
`KeyframeSequenceProvider:RegisterKeyframeSequence`; live games need the published asset id.

## Thinking levels (added in v1, optional)

`GET /health` adds `thinking_levels: [{"id", "label", "description"}]`. `/chat` replies add:

```json
"thinking": {"level": "high", "summary": "...", "elapsed_ms": 18234,
             "steps": [{"label": "Sample candidates", "detail": "8 candidate(s) ...", "ms": 17000}]}
```

Steps are only those that actually ran, with measured wall time. A level buys real compute,
not hidden chain-of-thought:

| id | samples | max new tokens | re-rank by likelihood | retrieval k |
|----|---------|----------------|-----------------------|-------------|
| low | 1 | 384 | no | 1 |
| medium | 4 | 512 | no | 3 |
| high | 8 | 640 | yes | 4 |
| xhigh | 16 | 704 | yes | 5 |
| max | 32 | 736 | yes | 6 |

## Hosting (added in v1)

The server reads `GODMODE_HOST`, `PORT`, `GODMODE_TOKEN` and `GODMODE_CKPT`. It refuses to bind a
non-local address without a token unless started with `--allow-no-token`. See `docs/ONLINE_MODE.md`.
