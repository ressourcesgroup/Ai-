--!strict
-- ChatUI: the dock-widget chat surface. It owns layout only; Main wires callbacks
-- to the HTTP client and the action pipeline.
--
--  +---------------------------------------------+
--  | (o) Connected  [3 - Engineer v] [Gear] [Clr] |  header
--  | Server URL [....]  Token [....]  [Save]      |  settings (collapsible)
--  |---------------------------------------------|
--  | message list (user / assistant bubbles,      |
--  | action cards, system notices)                |
--  |---------------------------------------------|
--  | [ Ask GODMODE...                  ] [Send]   |  input
--  +---------------------------------------------+

local Theme = require(script.Parent.Theme)
local Ui = require(script.Parent.Ui)
local Markdown = require(script.Parent.Markdown)

local ChatUI = {}
ChatUI.__index = ChatUI

export type Callbacks = {
	onSend: (text: string) -> (),
	onAutonomyChanged: (level: number) -> (),
	onSaveSettings: (url: string, token: string) -> (),
	onRefreshStatus: () -> (),
	onClear: () -> (),
}

export type Initial = {
	url: string,
	token: string,
	autonomy: number,
}

export type NoticeKind = "info" | "success" | "warning" | "error"
export type Status = "connected" | "disconnected" | "checking"

ChatUI.AUTONOMY_LABELS = { "Assistant", "Copilot", "Engineer", "Autonomous", "Research" }
ChatUI.AUTONOMY_HINTS = {
	"Answers questions; never proposes changes to your place.",
	"Proposes changes; you approve every action.",
	"Writes and verifies code; you approve every action.",
	"Safe, verified actions are applied automatically; destructive ones still need confirmation.",
	"Long multi-step work; same approval rules as Autonomous.",
}

local HEADER_HEIGHT = 34
local SETTINGS_HEIGHT = 96
local INPUT_HEIGHT = 64
local CODE_PREVIEW_LINES = 60

type State = {
	root: Frame,
	header: Frame,
	settings: Frame,
	messages: ScrollingFrame,
	inputBar: Frame,
	input: TextBox,
	sendButton: TextButton,
	statusDot: Frame,
	statusLabel: TextLabel,
	autonomyButton: TextButton,
	autonomyMenu: Frame,
	urlBox: TextBox,
	tokenBox: TextBox,
	callbacks: Callbacks,
	autonomy: number,
	order: number,
	busy: boolean,
	settingsOpen: boolean,
}

export type ChatUI = typeof(setmetatable({} :: State, ChatUI))

local function autonomyText(level: number): string
	return tostring(level) .. " - " .. ChatUI.AUTONOMY_LABELS[level] .. "  v"
end

function ChatUI.new(parent: Instance, callbacks: Callbacks, initial: Initial): ChatUI
	local root = Ui.create("Frame", {
		Name = "GodmodeRoot",
		Size = UDim2.fromScale(1, 1),
		BorderSizePixel = 0,
		Parent = parent,
	}) :: Frame
	Theme.paint(root, "BackgroundColor3", Theme.background)

	-- Header ---------------------------------------------------------------
	local header = Ui.create("Frame", {
		Name = "Header",
		Size = UDim2.new(1, 0, 0, HEADER_HEIGHT),
		BorderSizePixel = 0,
		ZIndex = 2,
		Parent = root,
	}, { Ui.padding(4), Ui.hlist(6) }) :: Frame
	Theme.paint(header, "BackgroundColor3", Theme.panel)

	local statusDot = Ui.create("Frame", {
		Name = "StatusDot",
		Size = UDim2.fromOffset(10, 10),
		BorderSizePixel = 0,
		BackgroundColor3 = Color3.fromRGB(150, 150, 150),
		LayoutOrder = 1,
		Parent = header,
	}, { Ui.corner(5) }) :: Frame

	local statusLabel = Ui.label("Checking...", {
		Size = UDim2.new(0, 90, 0, 24),
		AutomaticSize = Enum.AutomaticSize.None,
		TextYAlignment = Enum.TextYAlignment.Center,
		TextTruncate = Enum.TextTruncate.AtEnd,
		TextWrapped = false,
		TextSize = 14,
		LayoutOrder = 2,
	})
	statusLabel.Parent = header

	local autonomyButton = Ui.button(autonomyText(initial.autonomy), "normal", 120)
	autonomyButton.LayoutOrder = 3
	autonomyButton.Parent = header

	local settingsButton = Ui.button("Settings", "normal", 60)
	settingsButton.LayoutOrder = 4
	settingsButton.Parent = header

	local clearButton = Ui.button("Clear", "normal", 44)
	clearButton.LayoutOrder = 5
	clearButton.Parent = header

	-- Autonomy dropdown menu (overlay) -------------------------------------
	local autonomyMenu = Ui.create("Frame", {
		Name = "AutonomyMenu",
		Size = UDim2.fromOffset(300, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Position = UDim2.fromOffset(4, HEADER_HEIGHT),
		Visible = false,
		ZIndex = 10,
		BorderSizePixel = 0,
		Parent = root,
	}, { Ui.padding(4), Ui.vlist(2), Ui.corner(4) }) :: Frame
	Theme.paint(autonomyMenu, "BackgroundColor3", Theme.panel)
	Ui.stroke(Theme.border).Parent = autonomyMenu

	-- Settings panel --------------------------------------------------------
	local settings = Ui.create("Frame", {
		Name = "Settings",
		Size = UDim2.new(1, 0, 0, SETTINGS_HEIGHT),
		Position = UDim2.fromOffset(0, HEADER_HEIGHT),
		Visible = false,
		BorderSizePixel = 0,
		Parent = root,
	}, { Ui.padding(6), Ui.vlist(4) }) :: Frame
	Theme.paint(settings, "BackgroundColor3", Theme.panel)

	local urlBox = Ui.textBox("Server URL, e.g. http://127.0.0.1:8765")
	urlBox.Text = initial.url
	urlBox.LayoutOrder = 1
	urlBox.Parent = settings

	local tokenBox = Ui.textBox("Access token (optional)")
	tokenBox.Text = initial.token
	tokenBox.LayoutOrder = 2
	tokenBox.Parent = settings

	local saveRow = Ui.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 26),
		LayoutOrder = 3,
		Parent = settings,
	}, { Ui.hlist(6) })
	local saveButton = Ui.button("Save & Reconnect", "primary", 130)
	saveButton.Parent = saveRow

	-- Message list ------------------------------------------------------------
	local messages = Ui.create("ScrollingFrame", {
		Name = "Messages",
		BackgroundTransparency = 1,
		BorderSizePixel = 0,
		Position = UDim2.fromOffset(0, HEADER_HEIGHT),
		Size = UDim2.new(1, 0, 1, -(HEADER_HEIGHT + INPUT_HEIGHT)),
		CanvasSize = UDim2.new(),
		AutomaticCanvasSize = Enum.AutomaticSize.Y,
		ScrollingDirection = Enum.ScrollingDirection.Y,
		ScrollBarThickness = 8,
		VerticalScrollBarInset = Enum.ScrollBarInset.ScrollBar,
		Parent = root,
	}, { Ui.padding(8), Ui.vlist(8) }) :: ScrollingFrame
	Theme.paint(messages, "ScrollBarImageColor3", Theme.dimText)

	-- Input bar ---------------------------------------------------------------
	local inputBar = Ui.create("Frame", {
		Name = "InputBar",
		AnchorPoint = Vector2.new(0, 1),
		Position = UDim2.fromScale(0, 1),
		Size = UDim2.new(1, 0, 0, INPUT_HEIGHT),
		BorderSizePixel = 0,
		Parent = root,
	}, { Ui.padding(6) }) :: Frame
	Theme.paint(inputBar, "BackgroundColor3", Theme.panel)

	local input = Ui.textBox("Ask GODMODE... (Enter to send)")
	input.Size = UDim2.new(1, -76, 1, 0)
	input.Parent = inputBar

	local sendButton = Ui.button("Send", "primary", 70)
	sendButton.AnchorPoint = Vector2.new(1, 0)
	sendButton.Position = UDim2.fromScale(1, 0)
	sendButton.Size = UDim2.new(0, 70, 1, 0)
	sendButton.Parent = inputBar

	local self = setmetatable({
		root = root,
		header = header,
		settings = settings,
		messages = messages,
		inputBar = inputBar,
		input = input,
		sendButton = sendButton,
		statusDot = statusDot,
		statusLabel = statusLabel,
		autonomyButton = autonomyButton,
		autonomyMenu = autonomyMenu,
		urlBox = urlBox,
		tokenBox = tokenBox,
		callbacks = callbacks,
		autonomy = initial.autonomy,
		order = 0,
		busy = false,
		settingsOpen = false,
	} :: State, ChatUI)

	-- Autonomy menu entries.
	for level, name in ipairs(ChatUI.AUTONOMY_LABELS) do
		local entry = Ui.create("TextButton", {
			Size = UDim2.new(1, 0, 0, 0),
			AutomaticSize = Enum.AutomaticSize.Y,
			BackgroundTransparency = 1,
			Text = "",
			ZIndex = 11,
			LayoutOrder = level,
			Parent = autonomyMenu,
		}, { Ui.padding(4), Ui.vlist(0) }) :: TextButton
		local title = Ui.label(tostring(level) .. " - " .. name, {
			Font = Enum.Font.SourceSansSemibold,
			ZIndex = 11,
		})
		title.Parent = entry
		local hint = Ui.label(ChatUI.AUTONOMY_HINTS[level], { TextSize = 13, ZIndex = 11 })
		Theme.paint(hint, "TextColor3", Theme.dimText)
		hint.Parent = entry
		entry.MouseButton1Click:Connect(function()
			self:setAutonomy(level)
			autonomyMenu.Visible = false
			callbacks.onAutonomyChanged(level)
		end)
	end

	autonomyButton.MouseButton1Click:Connect(function()
		autonomyMenu.Visible = not autonomyMenu.Visible
	end)

	settingsButton.MouseButton1Click:Connect(function()
		self:setSettingsOpen(not self.settingsOpen)
	end)

	clearButton.MouseButton1Click:Connect(function()
		callbacks.onClear()
	end)

	saveButton.MouseButton1Click:Connect(function()
		callbacks.onSaveSettings(urlBox.Text, tokenBox.Text)
		self:setSettingsOpen(false)
	end)

	statusLabel.InputBegan:Connect(function(inputObject: InputObject)
		if inputObject.UserInputType == Enum.UserInputType.MouseButton1 then
			callbacks.onRefreshStatus()
		end
	end)

	local function submit()
		if self.busy then
			return
		end
		local text = input.Text:match("^%s*(.-)%s*$") or ""
		if text == "" then
			return
		end
		input.Text = ""
		callbacks.onSend(text)
	end

	input.FocusLost:Connect(function(enterPressed: boolean)
		if enterPressed then
			submit()
		end
	end)
	sendButton.MouseButton1Click:Connect(submit)

	return self
end

function ChatUI.setSettingsOpen(self: ChatUI, open: boolean): ()
	self.settingsOpen = open
	self.settings.Visible = open
	local top = HEADER_HEIGHT + (if open then SETTINGS_HEIGHT else 0)
	self.messages.Position = UDim2.fromOffset(0, top)
	self.messages.Size = UDim2.new(1, 0, 1, -(top + INPUT_HEIGHT))
end

function ChatUI.setAutonomy(self: ChatUI, level: number): ()
	self.autonomy = level
	self.autonomyButton.Text = autonomyText(level)
end

function ChatUI.setStatus(self: ChatUI, status: Status, detail: string?): ()
	if status == "connected" then
		self.statusDot.BackgroundColor3 = Theme.success()
		self.statusLabel.Text = detail or "Connected"
	elseif status == "checking" then
		self.statusDot.BackgroundColor3 = Theme.warning()
		self.statusLabel.Text = detail or "Checking..."
	else
		self.statusDot.BackgroundColor3 = Theme.danger()
		self.statusLabel.Text = detail or "Offline (click to retry)"
	end
end

function ChatUI.setBusy(self: ChatUI, busy: boolean): ()
	self.busy = busy
	self.sendButton.Text = if busy then "..." else "Send"
	self.sendButton.Active = not busy
	self.sendButton.AutoButtonColor = not busy
end

function ChatUI.nextOrder(self: ChatUI): number
	self.order += 1
	return self.order
end

-- Append any frame (e.g. an action card) to the message list.
function ChatUI.append(self: ChatUI, frame: GuiObject): ()
	frame.LayoutOrder = self:nextOrder()
	frame.Parent = self.messages
	self:scrollToBottom()
end

function ChatUI.scrollToBottom(self: ChatUI): ()
	task.defer(function()
		local messages = self.messages
		local maxY = math.max(0, messages.AbsoluteCanvasSize.Y - messages.AbsoluteWindowSize.Y)
		messages.CanvasPosition = Vector2.new(0, maxY)
	end)
end

local function bubble(role: "user" | "assistant"): Frame
	local frame = Ui.create("Frame", {
		Name = if role == "user" then "UserMessage" else "AssistantMessage",
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		BorderSizePixel = 0,
	}, { Ui.corner(6), Ui.padding(8), Ui.vlist(6) }) :: Frame
	Theme.paint(frame, "BackgroundColor3", if role == "user" then Theme.userBubble else Theme.assistantBubble)
	local who = Ui.label(if role == "user" then "You" else "GODMODE", {
		Font = Enum.Font.SourceSansBold,
		TextSize = 13,
		LayoutOrder = 0,
	})
	Theme.paint(who, "TextColor3", Theme.dimText)
	who.Parent = frame
	return frame
end

function ChatUI.addUserMessage(self: ChatUI, text: string): Frame
	local frame = bubble("user")
	local body = Ui.label(text, { LayoutOrder = 1 })
	body.Parent = frame
	self:append(frame)
	return frame
end

-- Render markdown-lite: prose as RichText labels, fenced code as monospace blocks.
function ChatUI.addAssistantMessage(self: ChatUI, markdown: string): Frame
	local frame = bubble("assistant")
	local order = 1
	for _, segment in ipairs(Markdown.parse(markdown)) do
		local element: GuiObject
		if segment.kind == "code" then
			element = Ui.codeBlock(segment.text, CODE_PREVIEW_LINES)
		else
			element = Ui.label(Markdown.toRichText(segment.text), { RichText = true })
		end
		element.LayoutOrder = order
		element.Parent = frame
		order += 1
	end
	self:append(frame)
	return frame
end

function ChatUI.addNotice(self: ChatUI, kind: NoticeKind, text: string): TextLabel
	local colorFn: () -> Color3 = Theme.dimText
	if kind == "error" then
		colorFn = Theme.error
	elseif kind == "warning" then
		colorFn = Theme.warning
	elseif kind == "success" then
		colorFn = Theme.success
	end
	local label = Ui.label(text, { TextSize = 14, TextColor3 = colorFn() })
	Theme.paint(label, "TextColor3", colorFn)
	self:append(label)
	return label
end

function ChatUI.clear(self: ChatUI): ()
	for _, child in ipairs(self.messages:GetChildren()) do
		if child:IsA("GuiObject") then
			child:Destroy()
		end
	end
end

function ChatUI.focusInput(self: ChatUI): ()
	self.input:CaptureFocus()
end

return ChatUI
