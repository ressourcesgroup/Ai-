--!strict
-- ChatUI: the dock-widget chat surface. It owns layout only; Main wires callbacks
-- to the HTTP client, the offline engine and the action pipeline.
--
--  +-------------------------------------------+
--  | GODMODE  (o Offline)              [⚙] [+] |  header
--  |-------------------------------------------|
--  |                     [ user bubble      ]  |
--  | ▸ Thought for 0.0s · 5 steps              |  assistant: full width
--  | Markdown reply, headings, `code`          |
--  | +-- luau ------------- Select  Insert --+ |
--  | | dark code panel                       | |
--  | +---------------------------------------+ |
--  | (offline:obby) (✓ 3/3 checks)             |  footer chips
--  | [ action card: Apply / Discard ]          |
--  |-------------------------------------------|
--  | +---------------------------------------+ |
--  | | Ask GODMODE…                          | |  grows to ~6 lines
--  | | [Thinking: Medium ▾] [A2 ▾]    [Send] | |
--  | +---------------------------------------+ |
--  +-------------------------------------------+
--
-- The settings view (gear) and the empty state replace the message list while
-- shown. Connections made on instances inside the message list die with those
-- instances; anything else a message needs is tracked and disconnected by clear().

local TextService = game:GetService("TextService")
local TweenService = game:GetService("TweenService")
local UserInputService = game:GetService("UserInputService")

local Theme = require(script.Parent.Theme)
local Ui = require(script.Parent.Ui)
local Markdown = require(script.Parent.Markdown)

local ChatUI = {}
ChatUI.__index = ChatUI

export type Mode = "offline" | "online"
export type NoticeKind = "info" | "success" | "warning" | "error"
export type Status = "offline" | "online" | "checking" | "unreachable"

export type Callbacks = {
	onSend: (text: string) -> (),
	onAutonomyChanged: (level: number) -> (),
	onThinkingChanged: (id: string) -> (),
	onModeChanged: (mode: Mode) -> (),
	onSaveSettings: (url: string, token: string) -> (),
	onTestConnection: (url: string, token: string) -> (),
	onRefreshStatus: () -> (),
	onNewChat: () -> (),
}

export type Initial = {
	url: string,
	token: string,
	autonomy: number,
	thinking: string,
	mode: Mode,
}

export type ThinkingLevel = { id: string, label: string, description: string }

export type ThinkingStep = { label: string?, detail: string?, ms: number? }
export type Thinking = {
	level: string?,
	summary: string?,
	elapsed_ms: number?,
	steps: { ThinkingStep }?,
}

export type AssistantExtras = {
	thinking: any?,
	source: string?,
	verification: { any }?,
	onInsert: ((code: string, language: string?) -> ())?,
}

-- Exactly these five ids are sent as `thinking` in POST /chat.
ChatUI.THINKING_LEVELS = {
	{ id = "low", label = "Low", description = "Fastest. Asks the server for the least deliberation." },
	{ id = "medium", label = "Medium", description = "Balanced default." },
	{ id = "high", label = "High", description = "Asks the server to plan more before answering." },
	{ id = "xhigh", label = "Extra high", description = "Longer planning and self-checks; slower." },
	{ id = "max", label = "Max", description = "Slowest. The most deliberation the server allows." },
} :: { ThinkingLevel }
ChatUI.DEFAULT_THINKING = "medium"

ChatUI.AUTONOMY_LABELS = { "Assistant", "Copilot", "Engineer", "Autonomous", "Research" }
ChatUI.AUTONOMY_HINTS = {
	"Answers questions; never proposes changes to your place.",
	"Proposes changes; you approve every action.",
	"Writes and verifies code; you approve every action.",
	"Safe, verified actions can be applied together after one click; destructive ones still need confirmation.",
	"Long multi-step work; same approval rules as Autonomous.",
}

ChatUI.EXAMPLE_PROMPTS = {
	{ title = "Build an obby", hint = "Checkpoints, kill bricks and a finish line" },
	{ title = "Make a sprint animation", hint = "R15 keyframes you can preview and edit" },
	{ title = "Save player data", hint = "DataStore with retries and session safety" },
	{ title = "Make a leaderboard with coins", hint = "leaderstats plus coins to collect" },
}

local HEADER_HEIGHT = 40
local CODE_PREVIEW_LINES = 60
local MAX_INPUT_LINES = 6
local INPUT_TEXT_SIZE = 14
local USER_BUBBLE_FRACTION = 0.85
local TYPING_ORDER = 2 ^ 30

type State = {
	root: Frame,
	body: Frame,
	messages: ScrollingFrame,
	emptyState: Frame,
	settingsView: ScrollingFrame,
	composer: Frame,
	inputClip: Frame,
	input: TextBox,
	sendButton: TextButton,
	thinkingButton: TextButton,
	autonomyButton: TextButton,
	statusChip: TextButton,
	statusDot: Frame,
	statusLabel: TextLabel,
	tooltip: TextLabel,
	modeButtons: { [string]: TextButton },
	urlBox: TextBox,
	tokenBox: TextBox,
	testResult: TextLabel,
	callbacks: Callbacks,
	levels: { ThinkingLevel },
	autonomy: number,
	thinking: string,
	mode: Mode,
	order: number,
	messageCount: number,
	busy: boolean,
	settingsOpen: boolean,
	stickToBottom: boolean,
	scrollTween: Tween?,
	bubbleLimits: { UISizeConstraint },
	messageConnections: { RBXScriptConnection },
	typing: Frame?,
	typingTweens: { Tween },
	typingThread: thread?,
	closeMenu: (() -> ())?,
}

export type ChatUI = typeof(setmetatable({} :: State, ChatUI))

local function trim(text: string): string
	return text:match("^%s*(.-)%s*$") or ""
end

local function levelById(levels: { ThinkingLevel }, id: string): ThinkingLevel
	for _, level in ipairs(levels) do
		if level.id == id then
			return level
		end
	end
	return levels[2]
end

local function thinkingText(levels: { ThinkingLevel }, id: string): string
	return "Thinking: " .. levelById(levels, id).label .. "  ▾"
end

local function autonomyText(level: number): string
	return "A" .. tostring(level) .. "  ▾"
end

local function lineHeight(): number
	local ok, size = pcall(function()
		return TextService:GetTextSize("Ag", INPUT_TEXT_SIZE, Theme.FONT_REGULAR, Vector2.new(1000, 1000))
	end)
	if ok and typeof(size) == "Vector2" and size.Y > 0 then
		return size.Y
	end
	return INPUT_TEXT_SIZE + 4
end

---------------------------------------------------------------------------
-- Construction
---------------------------------------------------------------------------

function ChatUI.new(parent: Instance, callbacks: Callbacks, initial: Initial): ChatUI
	local root = Ui.create("Frame", {
		Name = "GodmodeRoot",
		Size = UDim2.fromScale(1, 1),
		BorderSizePixel = 0,
		Parent = parent,
	}) :: Frame
	Theme.paint(root, "BackgroundColor3", Theme.background)

	-- Header ------------------------------------------------------------------
	local header = Ui.create("Frame", {
		Name = "Header",
		Size = UDim2.new(1, 0, 0, HEADER_HEIGHT),
		BorderSizePixel = 0,
		Parent = root,
	}, { Ui.padding(0, 6, 0, 12) }) :: Frame
	Theme.paint(header, "BackgroundColor3", Theme.background)
	local divider = Ui.create("Frame", {
		Name = "Divider",
		Position = UDim2.fromOffset(0, HEADER_HEIGHT),
		Size = UDim2.new(1, 0, 0, 1),
		BorderSizePixel = 0,
		Parent = root,
	})
	Theme.paint(divider, "BackgroundColor3", Theme.hairline)

	local left = Ui.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, -64, 1, 0),
		Parent = header,
	}, { Ui.hlist(8) })
	local title = Ui.create("TextLabel", {
		Name = "Title",
		BackgroundTransparency = 1,
		Size = UDim2.fromOffset(0, 20),
		AutomaticSize = Enum.AutomaticSize.X,
		Font = Theme.FONT_BOLD,
		TextSize = 15,
		Text = "GODMODE",
		LayoutOrder = 1,
		Parent = left,
	})
	Theme.paint(title, "TextColor3", Theme.text)

	-- Status chip: coloured dot + text; click to re-check.
	local statusChip = Ui.create("TextButton", {
		Name = "Status",
		Size = UDim2.fromOffset(0, 22),
		AutomaticSize = Enum.AutomaticSize.X,
		AutoButtonColor = false,
		BorderSizePixel = 0,
		Text = "",
		LayoutOrder = 2,
		Parent = left,
	}, { Ui.corner(11), Ui.padding(0, 9, 0, 8), Ui.hlist(6) }) :: TextButton
	Theme.paint(statusChip, "BackgroundColor3", Theme.subtle)
	local statusDot = Ui.create("Frame", {
		Size = UDim2.fromOffset(7, 7),
		BorderSizePixel = 0,
		LayoutOrder = 1,
		Parent = statusChip,
	}, { Ui.corner(4) }) :: Frame
	local statusLabel = Ui.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.fromOffset(0, 22),
		AutomaticSize = Enum.AutomaticSize.X,
		Font = Theme.FONT_MEDIUM,
		TextSize = Ui.SMALL_TEXT,
		Text = "",
		LayoutOrder = 2,
		Parent = statusChip,
	}) :: TextLabel
	Theme.paint(statusLabel, "TextColor3", Theme.dimText)

	local right = Ui.create("Frame", {
		BackgroundTransparency = 1,
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.fromScale(1, 0.5),
		Size = UDim2.fromOffset(60, 28),
		Parent = header,
	}, { Ui.hlist(4, Enum.HorizontalAlignment.Right) })
	local gearButton = Ui.iconButton("⚙", "Settings")
	gearButton.LayoutOrder = 1
	gearButton.Parent = right
	local newChatButton = Ui.iconButton("+", "NewChat")
	newChatButton.TextSize = 20
	newChatButton.LayoutOrder = 2
	newChatButton.Parent = right

	-- Body: messages / empty state / settings share this area ------------------
	local body = Ui.create("Frame", {
		Name = "Body",
		BackgroundTransparency = 1,
		Position = UDim2.fromOffset(0, HEADER_HEIGHT + 1),
		Size = UDim2.new(1, 0, 1, -(HEADER_HEIGHT + 1 + 90)),
		ClipsDescendants = true,
		Parent = root,
	}) :: Frame

	local messages = Ui.create("ScrollingFrame", {
		Name = "Messages",
		BackgroundTransparency = 1,
		BorderSizePixel = 0,
		Size = UDim2.fromScale(1, 1),
		CanvasSize = UDim2.new(),
		AutomaticCanvasSize = Enum.AutomaticSize.Y,
		ScrollingDirection = Enum.ScrollingDirection.Y,
		ScrollBarThickness = 6,
		VerticalScrollBarInset = Enum.ScrollBarInset.ScrollBar,
		Visible = false,
		Parent = body,
	}, { Ui.padding(12, 12, 16, 12), Ui.vlist(12) }) :: ScrollingFrame
	Theme.paint(messages, "ScrollBarImageColor3", Theme.hairline)

	local emptyState = Ui.create("Frame", {
		Name = "EmptyState",
		BackgroundTransparency = 1,
		Size = UDim2.fromScale(1, 1),
		Parent = body,
	}, { Ui.padding(16) }) :: Frame

	local settingsView = Ui.create("ScrollingFrame", {
		Name = "Settings",
		BackgroundTransparency = 1,
		BorderSizePixel = 0,
		Size = UDim2.fromScale(1, 1),
		CanvasSize = UDim2.new(),
		AutomaticCanvasSize = Enum.AutomaticSize.Y,
		ScrollingDirection = Enum.ScrollingDirection.Y,
		ScrollBarThickness = 6,
		VerticalScrollBarInset = Enum.ScrollBarInset.ScrollBar,
		Visible = false,
		Parent = body,
	}, { Ui.padding(14), Ui.vlist(8) }) :: ScrollingFrame
	Theme.paint(settingsView, "ScrollBarImageColor3", Theme.hairline)

	-- Composer --------------------------------------------------------------
	local composer = Ui.create("Frame", {
		Name = "Composer",
		BackgroundTransparency = 1,
		AnchorPoint = Vector2.new(0, 1),
		Position = UDim2.fromScale(0, 1),
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Parent = root,
	}, { Ui.padding(4, 10, 10, 10) }) :: Frame

	local card = Ui.create("Frame", {
		Name = "Card",
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		BorderSizePixel = 0,
		Parent = composer,
	}, { Ui.corner(10), Ui.padding(8, 8, 6, 10), Ui.vlist(6) }) :: Frame
	Theme.paint(card, "BackgroundColor3", Theme.inputBackground)
	local cardStroke = Ui.stroke(Theme.hairline)
	cardStroke.Parent = card

	local oneLine = lineHeight()
	local inputClip = Ui.create("Frame", {
		Name = "InputClip",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, oneLine),
		ClipsDescendants = true,
		LayoutOrder = 1,
		Parent = card,
	}) :: Frame
	local input = Ui.create("TextBox", {
		Name = "Input",
		BackgroundTransparency = 1,
		Size = UDim2.fromScale(1, 1),
		Font = Theme.FONT_REGULAR,
		TextSize = INPUT_TEXT_SIZE,
		Text = "",
		PlaceholderText = "Ask GODMODE…  (Enter to send, Shift+Enter for a new line)",
		ClearTextOnFocus = false,
		MultiLine = true,
		TextWrapped = true,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextYAlignment = Enum.TextYAlignment.Top,
		Parent = inputClip,
	}) :: TextBox
	Theme.paint(input, "TextColor3", Theme.text)
	Theme.paint(input, "PlaceholderColor3", Theme.dimText)

	local toolbarRow = Ui.create("Frame", {
		Name = "Toolbar",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, Ui.BUTTON_HEIGHT),
		LayoutOrder = 2,
		Parent = card,
	}) :: Frame
	local pickers = Ui.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, -70, 1, 0),
		Parent = toolbarRow,
	}, { Ui.hlist(4) })

	-- Per-widget copy: /health may relabel these without touching the module constant.
	local levels: { ThinkingLevel } = {}
	for _, level in ipairs(ChatUI.THINKING_LEVELS) do
		table.insert(levels, table.clone(level))
	end
	local thinkingButton = Ui.button(thinkingText(levels, initial.thinking), "ghost")
	thinkingButton.Name = "Thinking"
	thinkingButton.TextSize = Ui.SMALL_TEXT
	thinkingButton.LayoutOrder = 1
	thinkingButton.Parent = pickers
	local autonomyButton = Ui.button(autonomyText(initial.autonomy), "ghost")
	autonomyButton.Name = "Autonomy"
	autonomyButton.TextSize = Ui.SMALL_TEXT
	autonomyButton.LayoutOrder = 2
	autonomyButton.Parent = pickers

	local sendButton = Ui.button("Send", "primary", 62)
	sendButton.Name = "Send"
	sendButton.AnchorPoint = Vector2.new(1, 0)
	sendButton.Position = UDim2.fromScale(1, 0)
	sendButton.Parent = toolbarRow

	-- One shared tooltip (thinking level descriptions, autonomy hints).
	local tooltip = Ui.label("", {
		Name = "Tooltip",
		Size = UDim2.fromOffset(240, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		BackgroundTransparency = 0,
		BorderSizePixel = 0,
		TextSize = Ui.SMALL_TEXT,
		Visible = false,
		ZIndex = 40,
		Parent = root,
	})
	Ui.corner(6).Parent = tooltip
	Ui.padding(6, 8).Parent = tooltip
	Theme.paint(tooltip, "BackgroundColor3", Theme.raised)
	Ui.stroke(Theme.hairline).Parent = tooltip

	local self = setmetatable({
		root = root,
		body = body,
		messages = messages,
		emptyState = emptyState,
		settingsView = settingsView,
		composer = composer,
		inputClip = inputClip,
		input = input,
		sendButton = sendButton,
		thinkingButton = thinkingButton,
		autonomyButton = autonomyButton,
		statusChip = statusChip,
		statusDot = statusDot,
		statusLabel = statusLabel,
		tooltip = tooltip,
		modeButtons = {},
		urlBox = nil :: any,
		tokenBox = nil :: any,
		testResult = nil :: any,
		callbacks = callbacks,
		levels = levels,
		autonomy = initial.autonomy,
		thinking = levelById(levels, initial.thinking).id,
		mode = initial.mode,
		order = 0,
		messageCount = 0,
		busy = false,
		settingsOpen = false,
		stickToBottom = true,
		scrollTween = nil,
		bubbleLimits = {},
		messageConnections = {},
		typing = nil,
		typingTweens = {},
		typingThread = nil,
		closeMenu = nil,
	} :: State, ChatUI)

	self:buildEmptyState()
	self:buildSettings(initial)
	self:setStatus(if initial.mode == "online" then "checking" else "offline")

	-- Layout: the body fills whatever the (growing) composer leaves.
	local function layoutBody()
		local composerHeight = composer.AbsoluteSize.Y
		body.Size = UDim2.new(1, 0, 1, -(HEADER_HEIGHT + 1 + composerHeight))
	end
	composer:GetPropertyChangedSignal("AbsoluteSize"):Connect(layoutBody)
	layoutBody()

	-- User bubbles are capped at a fraction of the list width.
	messages:GetPropertyChangedSignal("AbsoluteSize"):Connect(function()
		local limit = self:bubbleLimit()
		for _, constraint in ipairs(self.bubbleLimits) do
			constraint.MaxSize = Vector2.new(limit, math.huge)
		end
	end)

	-- Follow new content while the user is at the bottom; stop when they scroll up.
	messages:GetPropertyChangedSignal("AbsoluteCanvasSize"):Connect(function()
		if self.stickToBottom then
			self:scrollToBottom(true)
		end
	end)
	messages.InputChanged:Connect(function(inputObject: InputObject)
		if inputObject.UserInputType == Enum.UserInputType.MouseWheel then
			task.defer(function()
				local maxY = math.max(0, messages.AbsoluteCanvasSize.Y - messages.AbsoluteWindowSize.Y)
				self.stickToBottom = messages.CanvasPosition.Y >= maxY - 24
			end)
		end
	end)

	-- Header buttons.
	gearButton.MouseButton1Click:Connect(function()
		self:setSettingsOpen(not self.settingsOpen)
	end)
	newChatButton.MouseButton1Click:Connect(function()
		callbacks.onNewChat()
	end)
	statusChip.MouseButton1Click:Connect(function()
		callbacks.onRefreshStatus()
	end)

	-- Pickers.
	thinkingButton.MouseButton1Click:Connect(function()
		self:hideTooltip()
		local items: { Ui.MenuItem } = {}
		for _, level in ipairs(self.levels) do
			table.insert(items, { id = level.id, label = level.label, description = level.description })
		end
		self.closeMenu = Ui.menu(root, thinkingButton, items, self.thinking, function(id: string)
			self:setThinking(id)
			callbacks.onThinkingChanged(id)
		end, 250)
	end)
	autonomyButton.MouseButton1Click:Connect(function()
		self:hideTooltip()
		local items: { Ui.MenuItem } = {}
		for level, name in ipairs(ChatUI.AUTONOMY_LABELS) do
			table.insert(items, {
				id = tostring(level),
				label = tostring(level) .. " · " .. name,
				description = ChatUI.AUTONOMY_HINTS[level],
			})
		end
		self.closeMenu = Ui.menu(root, autonomyButton, items, tostring(self.autonomy), function(id: string)
			local level = tonumber(id) :: number
			self:setAutonomy(level)
			callbacks.onAutonomyChanged(level)
		end, 270)
	end)
	thinkingButton.MouseEnter:Connect(function()
		self:showTooltip(thinkingButton, "Thinking level: " .. levelById(self.levels, self.thinking).description)
	end)
	thinkingButton.MouseLeave:Connect(function()
		self:hideTooltip()
	end)
	autonomyButton.MouseEnter:Connect(function()
		self:showTooltip(autonomyButton, "Autonomy " .. tostring(self.autonomy) .. " · "
			.. ChatUI.AUTONOMY_LABELS[self.autonomy] .. ": " .. ChatUI.AUTONOMY_HINTS[self.autonomy])
	end)
	autonomyButton.MouseLeave:Connect(function()
		self:hideTooltip()
	end)

	-- Composer: focus ring, auto-grow, Enter to send / Shift+Enter for a newline.
	input.Focused:Connect(function()
		Theme.paint(cardStroke, "Color", Theme.accent)
	end)
	input.FocusLost:Connect(function()
		Theme.paint(cardStroke, "Color", Theme.hairline)
	end)

	local function resizeInput()
		local wanted = math.max(oneLine, input.TextBounds.Y)
		local maxHeight = oneLine * MAX_INPUT_LINES
		inputClip.Size = UDim2.new(1, 0, 0, math.min(wanted, maxHeight))
		-- Past ~6 lines the box stops growing; keep the end (where you type) visible.
		input.TextYAlignment = if wanted > maxHeight then Enum.TextYAlignment.Bottom else Enum.TextYAlignment.Top
	end
	input:GetPropertyChangedSignal("TextBounds"):Connect(resizeInput)

	local shiftDown = false
	input.InputBegan:Connect(function(inputObject: InputObject)
		if inputObject.KeyCode == Enum.KeyCode.LeftShift or inputObject.KeyCode == Enum.KeyCode.RightShift then
			shiftDown = true
		end
	end)
	input.InputEnded:Connect(function(inputObject: InputObject)
		if inputObject.KeyCode == Enum.KeyCode.LeftShift or inputObject.KeyCode == Enum.KeyCode.RightShift then
			shiftDown = false
		end
	end)
	local function isShiftHeld(): boolean
		if shiftDown then
			return true
		end
		local ok, held = pcall(function()
			return UserInputService:IsKeyDown(Enum.KeyCode.LeftShift) or UserInputService:IsKeyDown(Enum.KeyCode.RightShift)
		end)
		return ok and held == true
	end

	local function submit()
		if self.busy then
			return
		end
		local text = trim(input.Text)
		if text == "" then
			return
		end
		input.Text = ""
		callbacks.onSend(text)
	end

	local previousText = ""
	local suppress = false
	input:GetPropertyChangedSignal("Text"):Connect(function()
		if suppress then
			previousText = input.Text
			return
		end
		local current = input.Text
		-- Exactly one newline typed (not pasted): find where it went in.
		if #current == #previousText + 1 then
			local i = 1
			while i <= #previousText and current:sub(i, i) == previousText:sub(i, i) do
				i += 1
			end
			if current:sub(i, i) == "\n" and current:sub(1, i - 1) .. current:sub(i + 1) == previousText and not isShiftHeld() then
				suppress = true
				input.Text = previousText
				suppress = false
				previousText = input.Text
				submit()
				return
			end
		end
		previousText = current
	end)
	sendButton.MouseButton1Click:Connect(submit)

	return self
end

---------------------------------------------------------------------------
-- Empty state and settings view
---------------------------------------------------------------------------

function ChatUI.buildEmptyState(self: ChatUI): ()
	local column = Ui.create("Frame", {
		BackgroundTransparency = 1,
		AnchorPoint = Vector2.new(0.5, 0.5),
		Position = UDim2.fromScale(0.5, 0.45),
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Parent = self.emptyState,
	}, { Ui.vlist(8, Enum.HorizontalAlignment.Center) })
	Ui.label("What should we build?", {
		Font = Theme.FONT_BOLD,
		TextSize = 18,
		TextXAlignment = Enum.TextXAlignment.Center,
		LayoutOrder = 1,
		Parent = column,
	})
	local subtitle = Ui.label("GODMODE proposes changes; nothing in your place changes until you click Apply, and every applied action is one undo step.", {
		TextSize = Ui.SMALL_TEXT,
		TextXAlignment = Enum.TextXAlignment.Center,
		LayoutOrder = 2,
		Parent = column,
	})
	Theme.paint(subtitle, "TextColor3", Theme.dimText)
	Ui.create("Frame", { BackgroundTransparency = 1, Size = UDim2.fromOffset(1, 4), LayoutOrder = 3, Parent = column })

	for index, prompt in ipairs(ChatUI.EXAMPLE_PROMPTS) do
		local button = Ui.create("TextButton", {
			Name = "Example" .. tostring(index),
			Size = UDim2.new(1, 0, 0, 0),
			AutomaticSize = Enum.AutomaticSize.Y,
			AutoButtonColor = false,
			BorderSizePixel = 0,
			Text = "",
			LayoutOrder = 10 + index,
			Parent = column,
		}, { Ui.corner(8), Ui.padding(8, 12), Ui.vlist(2) }) :: TextButton
		Theme.paint(button, "BackgroundColor3", Theme.raised)
		Ui.stroke(Theme.hairline).Parent = button
		Ui.hover(button, Theme.raised, Theme.subtle)
		Ui.label(prompt.title, { Font = Theme.FONT_MEDIUM, LayoutOrder = 1, Parent = button })
		local hint = Ui.label(prompt.hint, { TextSize = Ui.SMALL_TEXT, LayoutOrder = 2, Parent = button })
		Theme.paint(hint, "TextColor3", Theme.dimText)
		button.MouseButton1Click:Connect(function()
			if not self.busy then
				self.callbacks.onSend(string.lower(prompt.title))
			end
		end)
	end
end

local function sectionLabel(text: string, order: number, parent: Instance): TextLabel
	local label = Ui.label(text, { Font = Theme.FONT_MEDIUM, TextSize = Ui.SMALL_TEXT, LayoutOrder = order, Parent = parent })
	Theme.paint(label, "TextColor3", Theme.dimText)
	return label
end

function ChatUI.buildSettings(self: ChatUI, initial: Initial): ()
	local view = self.settingsView
	local titleRow = Ui.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 28),
		LayoutOrder = 1,
		Parent = view,
	})
	Ui.label("Settings", {
		Font = Theme.FONT_BOLD,
		TextSize = 16,
		Size = UDim2.new(1, -70, 1, 0),
		AutomaticSize = Enum.AutomaticSize.None,
		TextYAlignment = Enum.TextYAlignment.Center,
		Parent = titleRow,
	})
	local doneButton = Ui.button("Done", "secondary")
	doneButton.AnchorPoint = Vector2.new(1, 0)
	doneButton.Position = UDim2.fromScale(1, 0)
	doneButton.Parent = titleRow
	doneButton.MouseButton1Click:Connect(function()
		self:setSettingsOpen(false)
	end)

	-- Mode: segmented Offline | Online (applies immediately).
	sectionLabel("MODE", 10, view)
	local segmented = Ui.create("Frame", {
		Name = "Mode",
		Size = UDim2.new(1, 0, 0, 30),
		BorderSizePixel = 0,
		LayoutOrder = 11,
		Parent = view,
	}, { Ui.corner(8), Ui.padding(3) }) :: Frame
	Theme.paint(segmented, "BackgroundColor3", Theme.subtle)
	for index, mode in ipairs({ "offline", "online" }) do
		local button = Ui.create("TextButton", {
			Name = mode,
			Size = UDim2.new(0.5, if index == 1 then -2 else -1, 1, 0),
			Position = UDim2.new(if index == 1 then 0 else 0.5, if index == 1 then 0 else 1, 0, 0),
			AutoButtonColor = false,
			BorderSizePixel = 0,
			Font = Theme.FONT_MEDIUM,
			TextSize = 13,
			Text = if mode == "offline" then "Offline" else "Online",
			Parent = segmented,
		}, { Ui.corner(6) }) :: TextButton
		self.modeButtons[mode] = button
		button.MouseButton1Click:Connect(function()
			local picked: Mode = if mode == "online" then "online" else "offline"
			if picked ~= self.mode then
				self:setMode(picked)
				self.callbacks.onModeChanged(picked)
			end
		end)
	end
	local modeHint = Ui.label("Offline uses the built-in, pre-verified builds and makes no HTTP requests. Online sends each "
		.. "message to the GODMODE server below and falls back to offline if it cannot be reached.", {
		TextSize = Ui.SMALL_TEXT,
		LayoutOrder = 12,
		Parent = view,
	})
	Theme.paint(modeHint, "TextColor3", Theme.dimText)

	-- Server.
	sectionLabel("SERVER URL", 20, view)
	local urlBox = Ui.textBox("http://127.0.0.1:8765")
	urlBox.Text = initial.url
	urlBox.LayoutOrder = 21
	urlBox.Parent = view
	sectionLabel("ACCESS TOKEN (OPTIONAL)", 22, view)
	local tokenBox = Ui.textBox("Sent as the X-Godmode-Token header")
	tokenBox.Text = initial.token
	tokenBox.LayoutOrder = 23
	tokenBox.Parent = view

	local buttons = Ui.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, Ui.BUTTON_HEIGHT),
		LayoutOrder = 24,
		Parent = view,
	}, { Ui.hlist(6) })
	local testButton = Ui.button("Test connection", "secondary")
	testButton.LayoutOrder = 1
	testButton.Parent = buttons
	local saveButton = Ui.button("Save", "primary")
	saveButton.LayoutOrder = 2
	saveButton.Parent = buttons

	local testResult = Ui.label("", { TextSize = Ui.SMALL_TEXT, LayoutOrder = 25, Visible = false, Parent = view })

	local note = Ui.label("Studio asks once whether GODMODE may send HTTP requests to this host; choose Allow. "
		.. "If you dismissed the prompt, open Plugins > Manage Plugins and allow GODMODE's HTTP access. "
		.. "https:// URLs work too. A URL that is not on this computer receives your messages, the open script and the token.", {
		TextSize = Ui.SMALL_TEXT,
		LayoutOrder = 30,
		Parent = view,
	})
	Theme.paint(note, "TextColor3", Theme.dimText)

	self.urlBox = urlBox
	self.tokenBox = tokenBox
	self.testResult = testResult
	self:setMode(initial.mode)

	testButton.MouseButton1Click:Connect(function()
		self:setTestResult("info", "Testing " .. trim(urlBox.Text) .. "/health …")
		self.callbacks.onTestConnection(urlBox.Text, tokenBox.Text)
	end)
	saveButton.MouseButton1Click:Connect(function()
		self:setTestResult("success", "Saved.")
		self.callbacks.onSaveSettings(urlBox.Text, tokenBox.Text)
	end)
end

function ChatUI.setTestResult(self: ChatUI, kind: NoticeKind, text: string): ()
	local colorFn: () -> Color3 = Theme.dimText
	if kind == "error" then
		colorFn = Theme.error
	elseif kind == "warning" then
		colorFn = Theme.warning
	elseif kind == "success" then
		colorFn = Theme.success
	end
	self.testResult.Text = text
	self.testResult.Visible = true
	Theme.paint(self.testResult, "TextColor3", colorFn)
end

function ChatUI.setMode(self: ChatUI, mode: Mode): ()
	self.mode = mode
	for name, button in pairs(self.modeButtons) do
		if name == mode then
			button.BackgroundTransparency = 0
			Theme.paint(button, "BackgroundColor3", Theme.raised)
			Theme.paint(button, "TextColor3", Theme.text)
		else
			button.BackgroundTransparency = 1
			Theme.paint(button, "TextColor3", Theme.dimText)
		end
	end
end

function ChatUI.setSettingsOpen(self: ChatUI, open: boolean): ()
	self.settingsOpen = open
	if open then
		self.testResult.Visible = false
	end
	self:refreshBody()
end

function ChatUI.refreshBody(self: ChatUI): ()
	self.settingsView.Visible = self.settingsOpen
	self.emptyState.Visible = not self.settingsOpen and self.messageCount == 0
	self.messages.Visible = not self.settingsOpen and self.messageCount > 0
end

---------------------------------------------------------------------------
-- Header / composer state
---------------------------------------------------------------------------

function ChatUI.setStatus(self: ChatUI, status: Status, detail: string?): ()
	local colorFn: () -> Color3 = Theme.dimText
	local text = detail
	if status == "online" then
		colorFn = Theme.success
		text = text or "Online"
	elseif status == "checking" then
		colorFn = Theme.warning
		text = text or "Checking…"
	elseif status == "unreachable" then
		colorFn = Theme.danger
		text = text or "Unreachable"
	else
		text = text or "Offline"
	end
	Theme.paint(self.statusDot, "BackgroundColor3", colorFn)
	self.statusLabel.Text = text :: string
end

function ChatUI.setAutonomy(self: ChatUI, level: number): ()
	self.autonomy = level
	self.autonomyButton.Text = autonomyText(level)
end

function ChatUI.setThinking(self: ChatUI, id: string): ()
	self.thinking = levelById(self.levels, id).id
	self.thinkingButton.Text = thinkingText(self.levels, self.thinking)
end

-- Replace descriptions (and labels) with what /health `thinking_levels` reports.
-- Only the five known ids are accepted; anything else is ignored.
function ChatUI.setThinkingLevels(self: ChatUI, reported: any): ()
	if typeof(reported) ~= "table" then
		return
	end
	for _, entry in ipairs(reported) do
		if typeof(entry) == "table" and typeof(entry.id) == "string" then
			for _, level in ipairs(self.levels) do
				if level.id == entry.id then
					if typeof(entry.label) == "string" and entry.label ~= "" then
						level.label = entry.label
					end
					if typeof(entry.description) == "string" and entry.description ~= "" then
						level.description = entry.description
					end
				end
			end
		end
	end
	self.thinkingButton.Text = thinkingText(self.levels, self.thinking)
end

function ChatUI.thinkingLabel(self: ChatUI): string
	return levelById(self.levels, self.thinking).label
end

function ChatUI.showTooltip(self: ChatUI, anchor: GuiObject, text: string): ()
	local tooltip = self.tooltip
	tooltip.Text = text
	local relative = anchor.AbsolutePosition - self.root.AbsolutePosition
	local width = math.min(240, self.root.AbsoluteSize.X - 16)
	tooltip.Size = UDim2.fromOffset(width, 0)
	tooltip.AnchorPoint = Vector2.new(0, 1)
	tooltip.Position = UDim2.fromOffset(math.clamp(relative.X, 8, math.max(8, self.root.AbsoluteSize.X - width - 8)), relative.Y - 6)
	tooltip.Visible = true
end

function ChatUI.hideTooltip(self: ChatUI): ()
	self.tooltip.Visible = false
end

function ChatUI.setBusy(self: ChatUI, busy: boolean): ()
	self.busy = busy
	self.sendButton.Text = if busy then "…" else "Send"
	self.sendButton.Active = not busy
	self.sendButton.AutoButtonColor = not busy
	if busy then
		self:showTyping()
	else
		self:hideTyping()
	end
end

-- Animated "Thinking (Medium)… 1.4s" row: three tweened dots and a timer that
-- updates every 0.1 s. This is the only repeating loop in the UI; it stops in hideTyping.
function ChatUI.showTyping(self: ChatUI): ()
	self:hideTyping()
	local row = Ui.row(8, { Name = "Typing", LayoutOrder = TYPING_ORDER })
	local dots = Ui.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.fromOffset(30, 16),
		LayoutOrder = 1,
		Parent = row,
	}, { Ui.hlist(4) })
	for i = 1, 3 do
		local dot = Ui.create("Frame", {
			Size = UDim2.fromOffset(6, 6),
			BorderSizePixel = 0,
			LayoutOrder = i,
			Parent = dots,
		}, { Ui.corner(3) }) :: Frame
		Theme.paint(dot, "BackgroundColor3", Theme.accent)
		local tween = TweenService:Create(
			dot,
			TweenInfo.new(0.45, Enum.EasingStyle.Sine, Enum.EasingDirection.InOut, -1, true, (i - 1) * 0.15),
			{ BackgroundTransparency = 0.85 }
		)
		tween:Play()
		table.insert(self.typingTweens, tween)
	end
	local label = Ui.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.fromOffset(0, 16),
		AutomaticSize = Enum.AutomaticSize.X,
		Font = Theme.FONT_MEDIUM,
		TextSize = Ui.SMALL_TEXT,
		Text = "",
		LayoutOrder = 2,
		Parent = row,
	}) :: TextLabel
	Theme.paint(label, "TextColor3", Theme.dimText)

	local prefix = "Thinking (" .. self:thinkingLabel() .. ")… "
	local started = os.clock()
	label.Text = prefix .. "0.0s"
	self.typing = row
	self.messageCount += 1
	row.Parent = self.messages
	self:refreshBody()
	self.stickToBottom = true
	self:scrollToBottom()
	self.typingThread = task.spawn(function()
		while row.Parent ~= nil do
			task.wait(0.1)
			label.Text = prefix .. string.format("%.1fs", os.clock() - started)
		end
	end)
end

function ChatUI.hideTyping(self: ChatUI): ()
	local thread = self.typingThread
	self.typingThread = nil
	if thread and coroutine.status(thread) ~= "dead" and thread ~= coroutine.running() then
		pcall(task.cancel, thread)
	end
	for _, tween in ipairs(self.typingTweens) do
		tween:Cancel()
	end
	table.clear(self.typingTweens)
	if self.typing then
		self.typing:Destroy()
		self.typing = nil
		self.messageCount = math.max(0, self.messageCount - 1)
		self:refreshBody()
	end
end

---------------------------------------------------------------------------
-- Message list
---------------------------------------------------------------------------

function ChatUI.nextOrder(self: ChatUI): number
	self.order += 1
	return self.order
end

function ChatUI.bubbleLimit(self: ChatUI): number
	local width = self.messages.AbsoluteSize.X - 24 - self.messages.ScrollBarThickness
	return math.max(120, math.floor(width * USER_BUBBLE_FRACTION))
end

-- Append any frame (e.g. an action card) to the message list.
function ChatUI.append(self: ChatUI, frame: GuiObject): ()
	frame.LayoutOrder = self:nextOrder()
	frame.Parent = self.messages
	self.messageCount += 1
	self:refreshBody()
	self.stickToBottom = true
	self:scrollToBottom()
end

-- Smoothly scroll to the bottom once layout has settled (AutomaticCanvasSize
-- updates a frame later). `instant` skips the tween (used while content grows).
function ChatUI.scrollToBottom(self: ChatUI, instant: boolean?): ()
	task.defer(function()
		local messages = self.messages
		if messages.Parent == nil then
			return
		end
		local maxY = math.max(0, messages.AbsoluteCanvasSize.Y - messages.AbsoluteWindowSize.Y)
		local target = Vector2.new(0, maxY)
		if self.scrollTween then
			self.scrollTween:Cancel()
			self.scrollTween = nil
		end
		if instant or math.abs(messages.CanvasPosition.Y - maxY) < 4 then
			messages.CanvasPosition = target
			return
		end
		local tween = TweenService:Create(messages, TweenInfo.new(0.18, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), {
			CanvasPosition = target,
		})
		self.scrollTween = tween
		tween:Play()
	end)
end

function ChatUI.addUserMessage(self: ChatUI, text: string): Frame
	local row = Ui.create("Frame", {
		Name = "UserMessage",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
	}, { Ui.hlist(0, Enum.HorizontalAlignment.Right) }) :: Frame
	local bubble = Ui.create("Frame", {
		Name = "Bubble",
		Size = UDim2.fromOffset(0, 0),
		AutomaticSize = Enum.AutomaticSize.XY,
		BorderSizePixel = 0,
		Parent = row,
	}, { Ui.corner(12), Ui.padding(7, 12) }) :: Frame
	Theme.paint(bubble, "BackgroundColor3", Theme.userBubble)
	local label = Ui.label(text, {
		Size = UDim2.fromOffset(0, 0),
		AutomaticSize = Enum.AutomaticSize.XY,
		Parent = bubble,
	})
	local limit = Ui.create("UISizeConstraint", {
		MaxSize = Vector2.new(self:bubbleLimit() - 24, math.huge),
		Parent = label,
	}) :: UISizeConstraint
	table.insert(self.bubbleLimits, limit)
	self:append(row)
	return row
end

local function formatSeconds(ms: number): string
	return string.format("%.1fs", ms / 1000)
end

-- Collapsible "Thought for 1.2s · 6 steps" block; collapsed by default.
local function thinkingBlock(thinking: any, levels: { ThinkingLevel }): Frame?
	if typeof(thinking) ~= "table" then
		return nil
	end
	local steps = if typeof(thinking.steps) == "table" then thinking.steps else {}
	local elapsed = if typeof(thinking.elapsed_ms) == "number" then thinking.elapsed_ms else 0
	local header = "Thought for " .. formatSeconds(elapsed) .. " · " .. tostring(#steps) .. (if #steps == 1 then " step" else " steps")
	if typeof(thinking.level) == "string" then
		header ..= " · " .. levelById(levels, thinking.level).label
	end

	local frame = Ui.create("Frame", {
		Name = "Thinking",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
	}, { Ui.vlist(4) }) :: Frame
	local toggle = Ui.create("TextButton", {
		Name = "Toggle",
		BackgroundTransparency = 1,
		Size = UDim2.fromOffset(0, 20),
		AutomaticSize = Enum.AutomaticSize.X,
		AutoButtonColor = false,
		Font = Theme.FONT_MEDIUM,
		TextSize = Ui.SMALL_TEXT,
		TextXAlignment = Enum.TextXAlignment.Left,
		Text = "▸  " .. header,
		LayoutOrder = 1,
		Parent = frame,
	}) :: TextButton
	Theme.paint(toggle, "TextColor3", Theme.dimText)

	local details = Ui.create("Frame", {
		Name = "Steps",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Visible = false,
		LayoutOrder = 2,
		Parent = frame,
	}, { Ui.padding(2, 0, 2, 12), Ui.vlist(4) }) :: Frame
	local rail = Ui.create("Frame", {
		Size = UDim2.new(0, 2, 1, 0),
		Position = UDim2.fromOffset(-10, 0),
		BorderSizePixel = 0,
		Parent = details,
	})
	Theme.paint(rail, "BackgroundColor3", Theme.hairline)
	if typeof(thinking.summary) == "string" and thinking.summary ~= "" then
		local summary = Ui.label(thinking.summary, { TextSize = Ui.SMALL_TEXT, Font = Theme.FONT_MEDIUM, LayoutOrder = 0, Parent = details })
		Theme.paint(summary, "TextColor3", Theme.dimText)
	end
	for index, step in ipairs(steps) do
		if typeof(step) == "table" then
			local text = Markdown.escape(tostring(step.label or ("Step " .. tostring(index))))
			if typeof(step.detail) == "string" and step.detail ~= "" then
				text ..= ' <font transparency="0.35">· ' .. Markdown.escape(step.detail) .. "</font>"
			end
			if typeof(step.ms) == "number" then
				text ..= ' <font transparency="0.5">(' .. tostring(math.floor(step.ms)) .. " ms)</font>"
			end
			local line = Ui.label(tostring(index) .. ".  " .. text, {
				RichText = true,
				TextSize = Ui.SMALL_TEXT,
				LayoutOrder = index,
				Parent = details,
			})
			Theme.paint(line, "TextColor3", Theme.dimText)
		end
	end
	toggle.MouseButton1Click:Connect(function()
		details.Visible = not details.Visible
		toggle.Text = (if details.Visible then "▾  " else "▸  ") .. header
	end)
	return frame
end

-- Render an assistant reply: optional thinking block, markdown (headings, prose,
-- code panels with Select / Insert), then a footer of chips (source, checks).
function ChatUI.addAssistantMessage(self: ChatUI, markdown: string, extras: AssistantExtras?): Frame
	local frame = Ui.create("Frame", {
		Name = "AssistantMessage",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
	}, { Ui.vlist(8) }) :: Frame
	local order = 1
	local info = extras or {}

	local block = thinkingBlock(info.thinking, self.levels)
	if block then
		block.LayoutOrder = order
		block.Parent = frame
		order += 1
	end

	local codeHex = Theme.inlineCodeHex()
	for _, segment in ipairs(Markdown.parse(markdown)) do
		local element: GuiObject
		if segment.kind == "code" then
			element = Ui.codeBlock(segment.text, CODE_PREVIEW_LINES, { language = segment.language, onInsert = info.onInsert })
		elseif segment.kind == "heading" then
			local level = segment.level or 2
			element = Ui.label(Markdown.toRichText(segment.text, codeHex), {
				RichText = true,
				Font = Theme.FONT_BOLD,
				TextSize = if level == 1 then 18 elseif level == 2 then 16 else 14,
			})
		else
			element = Ui.label(Markdown.toRichText(segment.text, codeHex), { RichText = true, LineHeight = 1.1 })
		end
		element.LayoutOrder = order
		element.Parent = frame
		order += 1
	end

	-- Footer chips.
	local chips: { Frame } = {}
	if typeof(info.source) == "string" and info.source ~= "" then
		local chip = Ui.chip(info.source)
		chip.Name = "Source"
		table.insert(chips, chip)
	end
	local verification = info.verification
	if typeof(verification) == "table" and #verification > 0 then
		local passed, total = 0, 0
		for _, entry in ipairs(verification) do
			if typeof(entry) == "table" then
				total += 1
				if entry.ok == true then
					passed += 1
				end
			end
		end
		if total > 0 then
			local ok = passed == total
			local chip = Ui.chip((if ok then "✓ " else "✕ ") .. tostring(passed) .. "/" .. tostring(total) .. " checks",
				if ok then Theme.success else Theme.warning)
			chip.Name = "Checks"
			table.insert(chips, chip)
		end
	end
	if #chips > 0 then
		local footer = Ui.row(6, { Name = "Footer", LayoutOrder = order })
		for index, chip in ipairs(chips) do
			chip.LayoutOrder = index
			chip.Parent = footer
		end
		footer.Parent = frame
	end

	self:append(frame)
	return frame
end

function ChatUI.addNotice(self: ChatUI, kind: NoticeKind, text: string): Frame
	local colorFn: () -> Color3 = Theme.dimText
	local glyph = "ℹ"
	if kind == "error" then
		colorFn, glyph = Theme.error, "✕"
	elseif kind == "warning" then
		colorFn, glyph = Theme.warning, "!"
	elseif kind == "success" then
		colorFn, glyph = Theme.success, "✓"
	end
	local row = Ui.create("Frame", {
		Name = "Notice",
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		BorderSizePixel = 0,
	}, { Ui.corner(6), Ui.padding(6, 10) }) :: Frame
	Theme.paint(row, "BackgroundColor3", Theme.subtle)
	local label = Ui.label(glyph .. "  " .. text, { TextSize = Ui.SMALL_TEXT, TextColor3 = colorFn(), Parent = row })
	Theme.paint(label, "TextColor3", colorFn)
	self:append(row)
	return row
end

-- Remember a connection to something outside the message list so clear() can
-- disconnect it (connections on message instances die with Destroy()).
function ChatUI.track(self: ChatUI, connection: RBXScriptConnection): ()
	table.insert(self.messageConnections, connection)
end

-- New chat: drop every message, its connections and the typing indicator.
function ChatUI.clear(self: ChatUI): ()
	self:hideTyping()
	if self.closeMenu then
		self.closeMenu()
		self.closeMenu = nil
	end
	for _, connection in ipairs(self.messageConnections) do
		connection:Disconnect()
	end
	table.clear(self.messageConnections)
	table.clear(self.bubbleLimits)
	for _, child in ipairs(self.messages:GetChildren()) do
		if child:IsA("GuiObject") then
			child:Destroy()
		end
	end
	self.order = 0
	self.messageCount = 0
	self.stickToBottom = true
	self.messages.CanvasPosition = Vector2.zero
	self:refreshBody()
end

function ChatUI.focusInput(self: ChatUI): ()
	self.input:CaptureFocus()
end

return ChatUI
