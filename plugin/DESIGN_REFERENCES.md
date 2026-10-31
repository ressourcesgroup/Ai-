# GODMODE panel: design references

The panel redesign (Theme.luau, Ui.luau, Markdown.luau, ChatUI.luau, ActionPreview.luau)
borrows interaction patterns from current AI chat panels. No branding, logos, icons,
names or colours from these products were copied: every colour comes from the Studio
theme (`settings().Studio.Theme`) or from a neutral fixed value, and every glyph is a
plain Unicode character.

## What was looked at

| Product | Source | Read on |
|---|---|---|
| Roblox Studio Assistant | https://create.roblox.com/docs/en-us/assistant/guide.md | 2026-10 |
| Roblox Studio UI overview (docking, panels) | https://create.roblox.com/docs/en-us/studio/ui-overview.md | 2026-10 |
| VS Code Copilot Chat | https://code.visualstudio.com/docs/copilot/chat/copilot-chat | 2026-10 |
| VS Code language models / thinking effort picker | https://code.visualstudio.com/docs/agent-customization/language-models | 2026-10 |
| Cursor agent side panel | https://cursor.com/docs/agent/overview | 2026-10 |
| Claude.ai model / effort / extended thinking | https://support.claude.com/en/articles/8664678-change-the-model-effort-and-extended-thinking-settings | 2026-10 |
| ChatGPT composer (general familiarity, no page copied) | https://chatgpt.com | - |

All of these are documentation pages and help-centre articles, not screenshots. The
layout below was made to fit a narrow (300-400 px) Studio dock widget.

## Patterns adopted

### Header
* **Compact title bar with a "new chat" button at the top** (Cursor, Roblox Assistant's
  chat list). GODMODE: title on the left, then a status chip, then a gear and a "+" button
  on the right. "+" clears the conversation and history and brings back the empty state.
* **Connection state as a small chip, not a sentence** (Copilot's model/status pill).
  It reads `Offline`, `Online · 127.0.0.1:8765`, `Checking…` or `Unreachable`, with a coloured dot.

### Messages
* **User turns are right-aligned rounded bubbles. Assistant turns are full width and have
  no bubble** (Claude.ai, ChatGPT, Copilot). This makes long code answers readable in
  a narrow panel.
* **A small provenance tag under each assistant reply** (Copilot's "used n references"
  footer). GODMODE shows the `source` (for example `offline:obby` or `model:...`) and a
  `✓ n/m checks` chip with the verification count. The old separate "Source:" notice
  line is gone.
* **Fenced code in a dark monospace panel with a header row naming the language and
  actions** (all four products). GODMODE's actions are **Select** (focuses the read-only
  box and selects everything, so Ctrl+C copies it; plugins cannot write to the system
  clipboard) and **Insert** (creates a Script from the block through the normal
  ActionApplier path, which takes one undo step). The panel is always dark, like an editor.
* **Markdown headings, bold, italics, inline code and lists** are rendered with RichText.
  Text is escaped first, so the server cannot inject markup.

### Thinking
* **Collapsible "Thinking" section above the answer, collapsed by default, with a
  duration summary** (Claude.ai "Thought for Ns", Copilot "Completed N steps").
  GODMODE: `▸ Thought for 1.2s · 6 steps`; when expanded it lists each step's label,
  detail and time. If a reply has no `thinking` object, no block is shown.
* **Live "Thinking…" indicator with a running timer while waiting** (Claude.ai). It
  shows three pulsing dots and `Thinking (Medium)… 1.4s`.

### Composer
* **Effort level picked next to the send button, with the label showing the current
  level** (Claude.ai effort selector; Copilot "Model · High" submenu). GODMODE has a
  `Thinking: Medium ▾` dropdown with exactly Low / Medium / High / Extra high / Max,
  and descriptions come from `/health` `thinking_levels` when the server provides them.
* **Mode / autonomy control as a compact pill in the composer** (Roblox Assistant's
  mode dropdown in the input panel). The 1-5 autonomy selector is a small `A2 ▾` pill
  with a popover that explains each level.
* **Enter sends, Shift+Enter adds a newline, and the box grows to about 6 lines**
  (Copilot, Cursor, Claude.ai). There is also a Send button as a fallback.
* **Slash commands keep working** (Roblox Assistant `/` commands): `/server on|off`.

### Empty state
* **Clickable starter prompts in an otherwise empty panel** (Copilot, Claude.ai, Roblox
  Assistant). There are four cards: build an obby, make a sprint animation, save player
  data, make a leaderboard with coins.

### Actions
* **Proposed changes are cards with explicit accept/reject buttons** (Roblox Assistant
  Confirm / Back, Cursor's review-before-apply, Copilot "Keep / Undo"). GODMODE cards
  have a type pill, a title, a description, the target path in monospace, a preview or
  diff, a `✓ n/m checks` badge that expands to the details, and **Apply** / **Discard** /
  **Re-verify** buttons.
* **Destructive actions are visually distinct and need two clicks.** The card has a red
  outline and a red **Delete…** button, followed by **Confirm delete** / **Cancel**.

### Settings
* **Settings live behind a gear and replace the message view** rather than squeezing a
  form into the header (Copilot and Cursor settings pages). The view contains a mode
  segmented control (Offline | Online), the server URL, an optional token, **Test
  connection** (calls `GET /health` and shows the version and whether the model is
  loaded) and **Save**. A note explains that Studio will ask for HTTP permission for
  that host.
