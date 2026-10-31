--!strict
-- GODMODE Studio plugin entry point (packaged as the Script "GodmodePlugin";
-- every other module is a ModuleScript child of it).
--
-- Flow: user message -> POST /chat (with selection / open-script context and the
-- autonomy level) -> reply bubble + one ActionPreview card per proposed action ->
-- user clicks Apply -> ActionApplier applies it as one undo step -> the real
-- outcome (success or the exact error) is reported in the chat.
-- The server only proposes; only this plugin changes the place, and only on a click.

local ActionApplier = require(script.ActionApplier)
local ActionPreview = require(script.ActionPreview)
local ChatUI = require(script.ChatUI)
local Context = require(script.Context)
local HttpClient = require(script.HttpClient)
local OfflineEngine = require(script.OfflineEngine)
local Theme = require(script.Theme)
local Ui = require(script.Ui)

local PROTOCOL_VERSION = 1
local DEFAULT_URL = "http://127.0.0.1:8765"
local DEFAULT_AUTONOMY = 2
local HEALTH_INTERVAL = 30
local MAX_HISTORY = 20
local MAX_MESSAGE_CHARS = 8000

local SETTING_URL = "GodmodeServerUrl"
local SETTING_TOKEN = "GodmodeToken"
local SETTING_AUTONOMY = "GodmodeAutonomy"
local SETTING_THINKING = "GodmodeThinking"

local thisPlugin: Plugin = plugin

---------------------------------------------------------------------------
-- Settings (persisted per user with plugin:SetSetting)
---------------------------------------------------------------------------

local function readSetting(key: string): any
	local ok, value = pcall(function()
		return thisPlugin:GetSetting(key)
	end)
	return if ok then value else nil
end

local function writeSetting(key: string, value: any)
	pcall(function()
		thisPlugin:SetSetting(key, value)
	end)
end

local serverUrl: string = DEFAULT_URL
local storedUrl = readSetting(SETTING_URL)
if typeof(storedUrl) == "string" and storedUrl ~= "" then
	serverUrl = storedUrl
end

local token: string = ""
local storedToken = readSetting(SETTING_TOKEN)
if typeof(storedToken) == "string" then
	token = storedToken
end

local autonomy: number = DEFAULT_AUTONOMY
local storedAutonomy = readSetting(SETTING_AUTONOMY)
if typeof(storedAutonomy) == "number" and storedAutonomy >= 1 and storedAutonomy <= 5 then
	autonomy = math.floor(storedAutonomy)
end

local thinkingLevel: string = ChatUI.DEFAULT_THINKING
local storedThinking = readSetting(SETTING_THINKING)
for _, level in ipairs(ChatUI.THINKING_LEVELS) do
	if storedThinking == level.id then
		thinkingLevel = level.id
	end
end

-- Every request goes to the configured base URL (Settings > Server URL).
local client = HttpClient.new(serverUrl, token)

-- Offline by default: the built-in engine needs no server and no HTTP permission.
-- Settings > Mode (or `/server on|off` in the chat) switches to the optional GODMODE server.
local SETTING_USE_SERVER = "godmode_use_server"
local useServer = readSetting(SETTING_USE_SERVER) == true

---------------------------------------------------------------------------
-- Toolbar button + dock widget
---------------------------------------------------------------------------

local toolbar = thisPlugin:CreateToolbar("GODMODE")
local toggleButton = toolbar:CreateButton("GODMODE", "Open the GODMODE assistant", "", "GODMODE")
toggleButton.ClickableWhenViewportHidden = true

local widgetInfo = DockWidgetPluginGuiInfo.new(
	Enum.InitialDockState.Right,
	false, -- initially enabled
	false, -- override saved enabled state
	380, -- default width
	560, -- default height
	300, -- minimum width
	300 -- minimum height
)
local widget = thisPlugin:CreateDockWidgetPluginGui("GodmodeAssistant", widgetInfo)
widget.Title = "GODMODE"
widget.Name = "GodmodeAssistant"
widget.ZIndexBehavior = Enum.ZIndexBehavior.Sibling

toggleButton:SetActive(widget.Enabled)
toggleButton.Click:Connect(function()
	widget.Enabled = not widget.Enabled
end)

---------------------------------------------------------------------------
-- Chat state
---------------------------------------------------------------------------

type HistoryEntry = { role: string, content: string }
local history: { HistoryEntry } = {}
local serverCompatible = true
local ui: ChatUI.ChatUI

local function pushHistory(role: string, content: string)
	table.insert(history, { role = role, content = content })
	while #history > MAX_HISTORY do
		table.remove(history, 1)
	end
end

local function trim(text: string): string
	return text:match("^%s*(.-)%s*$") or ""
end

local function isLocalUrl(url: string): boolean
	local host = url:match("^https?://([^/:]+)")
	return host == "127.0.0.1" or host == "localhost" or host == "[::1]"
end

local function hostOf(url: string): string
	return url:match("^https?://([^/]+)") or url
end

local function checkHealth()
	if not useServer then
		ui:setStatus("offline")
		return nil
	end
	ui:setStatus("checking")
	local response = client:get("/health")
	if not response.ok then
		serverCompatible = true -- unknown; let /chat report its own error
		ui:setStatus("unreachable", "Unreachable · " .. hostOf(serverUrl))
		return response.error
	end
	local body = response.body
	local protocol = if typeof(body) == "table" then body.protocol_version else nil
	if typeof(protocol) == "number" and protocol > PROTOCOL_VERSION then
		serverCompatible = false
		ui:setStatus("unreachable", "Protocol v" .. tostring(protocol))
		return "The server speaks protocol v" .. tostring(protocol)
			.. " but this plugin only understands v" .. tostring(PROTOCOL_VERSION) .. ". Update the plugin."
	end
	serverCompatible = true
	if typeof(body) == "table" then
		-- Optional: tooltips for the thinking picker.
		ui:setThinkingLevels(body.thinking_levels)
	end
	ui:setStatus("online", "Online · " .. hostOf(serverUrl))
	return nil
end

local function verifySource(source: string): (boolean, string)
	if not useServer then
		return true, "Offline mode: this script was already verified (structure + security lint) when the plugin was built. "
			.. "Re-verifying edited code needs the optional server (/server on)."
	end
	local response = client:post("/verify", { source = source })
	if not response.ok then
		return false, "Verification request failed: " .. tostring(response.error)
	end
	local body = response.body
	local lines: { string } = {}
	if typeof(body.verification) == "table" then
		for _, entry in ipairs(body.verification) do
			if typeof(entry) == "table" then
				table.insert(lines, (if entry.ok == true then "PASS " else "FAIL ") .. tostring(entry.check)
					.. (if typeof(entry.detail) == "string" then " - " .. entry.detail else ""))
			end
		end
	end
	if typeof(body.lint) == "table" then
		for _, finding in ipairs(body.lint) do
			if typeof(finding) == "table" then
				table.insert(lines, string.format("  %s %s line %s: %s", tostring(finding.rule), tostring(finding.severity),
					tostring(finding.line), tostring(finding.message)))
			end
		end
	end
	local passed = body.passed == true
	return passed, (if passed then "Verifier (/verify): passed" else "Verifier (/verify): not passed")
		.. (if #lines > 0 then "\n" .. table.concat(lines, "\n") else "")
end

local function reportResult(action: any, result: ActionApplier.Result)
	local label = tostring(action.type) .. " " .. ActionApplier.targetPath(action)
	if result.ok then
		ui:addNotice("success", "Applied " .. label .. ": " .. result.message)
	else
		ui:addNotice("error", "Did NOT apply " .. label .. ": " .. result.message)
	end
end

local function showActions(actions: { any }, verification: { any })
	local cards: { ActionPreview.Card } = {}
	for _, action in ipairs(actions) do
		local card = ActionPreview.new(action, {
			verification = verification,
			onApply = ActionApplier.apply,
			onVerify = verifySource,
			onResult = reportResult,
		})
		ui:append(card.frame)
		table.insert(cards, card)
	end

	-- Autonomy 4-5: the server marks safe actions requires_approval=false; the
	-- protocol allows applying those together after ONE confirmation click.
	local batchable = 0
	for _, card in ipairs(cards) do
		if card:isBatchable() then
			batchable += 1
		end
	end
	if autonomy >= 4 and batchable > 0 then
		local row = Ui.create("Frame", {
			BackgroundTransparency = 1,
			Size = UDim2.new(1, 0, 0, 26),
		}, { Ui.hlist(6) }) :: Frame
		local applyAll = Ui.button("Apply " .. tostring(batchable) .. " safe action(s) in order", "primary", 240)
		applyAll.Parent = row
		ui:append(row)
		applyAll.MouseButton1Click:Connect(function()
			applyAll:Destroy()
			local applied, failed = 0, false
			for _, card in ipairs(cards) do
				if card:isBatchable() then
					local result = card:apply()
					if result and result.ok then
						applied += 1
					elseif result then
						failed = true
						break
					end
				end
			end
			if failed then
				ui:addNotice("error", "Batch stopped after a failure: " .. tostring(applied)
					.. " action(s) applied, the rest were NOT applied (they may depend on the failed one).")
			else
				ui:addNotice("success", "Batch finished: " .. tostring(applied) .. " action(s) applied. Destructive actions (if any) still need their own confirmation.")
			end
		end)
	end
end

-- "Insert" on a code panel: create a Script from the block through the normal
-- ActionApplier path (one undo step), then report the real outcome.
local function insertSnippet(code: string, _language: string?)
	local action = {
		type = "create_script",
		class = "Script",
		name = "GodmodeSnippet",
		parent_path = "ServerScriptService",
		source = code,
	}
	reportResult(action, ActionApplier.apply(action))
end

local function setMode(mode: ChatUI.Mode)
	useServer = mode == "online"
	writeSetting(SETTING_USE_SERVER, useServer)
	ui:setMode(mode)
	task.spawn(function()
		local err = checkHealth()
		if err then
			ui:addNotice("warning", "Online mode, but the server did not answer: " .. tostring(err) .. " Replies will come from the offline engine until it does.")
		end
	end)
end

local function sendMessage(text: string)
	if #text > MAX_MESSAGE_CHARS then
		ui:addNotice("error", "Message is too long (" .. tostring(#text) .. " characters; the limit is " .. tostring(MAX_MESSAGE_CHARS) .. ").")
		return
	end
	local command = string.lower(text):match("^/server%s+(%a+)$")
	if command == "on" or command == "off" then
		useServer = command == "on"
		writeSetting(SETTING_USE_SERVER, useServer)
		ui:setMode(if useServer then "online" else "offline")
		ui:addNotice("info", if useServer
			then "Online mode: using the GODMODE server at " .. serverUrl .. " (start it with `python -m godmode.server`). Falls back to offline if unreachable."
			else "Offline mode: built-in tools and animations, no server needed.")
		task.spawn(checkHealth)
		return
	end
	if useServer and not serverCompatible then
		ui:addNotice("error", "This plugin is too old for the connected server's protocol version. Update the plugin.")
		return
	end
	ui:addUserMessage(text)
	ui:setBusy(true)
	local historySnapshot = table.clone(history)
	pushHistory("user", text)

	task.spawn(function()
		local payload = {
			message = text,
			history = historySnapshot,
			context = Context.collect(),
			autonomy = autonomy,
			thinking = thinkingLevel,
		}
		local response
		if useServer then
			response = client:post("/chat", payload)
			if not response.ok then
				ui:addNotice("warning", "Server unreachable (" .. tostring(response.error) .. "); answering offline.")
				response = { ok = true, body = OfflineEngine.chat(payload) }
			end
		else
			response = { ok = true, body = OfflineEngine.chat(payload) }
		end
		ui:setBusy(false)
		if not useServer then
			ui:setStatus("offline")
		end
		local body = response.body
		local reply = if typeof(body.reply_markdown) == "string" then body.reply_markdown else "(empty reply)"
		local verification = if typeof(body.verification) == "table" then body.verification else {}
		ui:addAssistantMessage(reply, {
			-- Rendered only when the reply carries a thinking object.
			thinking = body.thinking,
			source = if typeof(body.source) == "string" then body.source else nil,
			verification = verification,
			onInsert = insertSnippet,
		})
		pushHistory("assistant", reply)
		if typeof(body.warnings) == "table" then
			for _, warning in ipairs(body.warnings) do
				ui:addNotice("warning", "Warning: " .. tostring(warning))
			end
		end
		local actions = if typeof(body.actions) == "table" then body.actions else {}
		if #actions > 0 then
			showActions(actions, verification)
		end
	end)
end

ui = ChatUI.new(widget, {
	onSend = sendMessage,
	onAutonomyChanged = function(level: number)
		autonomy = level
		writeSetting(SETTING_AUTONOMY, level)
	end,
	onThinkingChanged = function(id: string)
		thinkingLevel = id
		writeSetting(SETTING_THINKING, id)
	end,
	onModeChanged = setMode,
	onSaveSettings = function(url: string, newToken: string)
		serverUrl = HttpClient.normaliseUrl(if trim(url) ~= "" then trim(url) else DEFAULT_URL)
		token = trim(newToken)
		writeSetting(SETTING_URL, serverUrl)
		writeSetting(SETTING_TOKEN, token)
		client = HttpClient.new(serverUrl, token)
		if not isLocalUrl(serverUrl) then
			ui:setTestResult("warning", "Saved. " .. serverUrl .. " is not on this computer: your messages, open script and token will be sent there.")
		end
		task.spawn(checkHealth)
	end,
	onTestConnection = function(url: string, testToken: string)
		local target = HttpClient.normaliseUrl(if trim(url) ~= "" then trim(url) else DEFAULT_URL)
		task.spawn(function()
			local response = HttpClient.new(target, trim(testToken), 10):get("/health")
			if not response.ok then
				ui:setTestResult("error", "Could not reach " .. target .. "/health: " .. tostring(response.error))
				return
			end
			local body = response.body
			if typeof(body) ~= "table" then
				ui:setTestResult("error", target .. "/health answered, but not with JSON.")
				return
			end
			local version = if body.version ~= nil then tostring(body.version) else "unknown"
			local loaded = typeof(body.model) == "table" and body.model.loaded == true
			local protocol = if typeof(body.protocol_version) == "number" then tostring(body.protocol_version) else "?"
			ui:setTestResult(if loaded then "success" else "warning", "Connected. Server version " .. version
				.. " · protocol v" .. protocol .. " · model loaded: " .. (if loaded then "yes" else "no (rule-based replies only)")
				.. (if isLocalUrl(target) then "" else "\nThis host is not on this computer."))
			ui:setThinkingLevels(body.thinking_levels)
		end)
	end,
	onRefreshStatus = function()
		task.spawn(function()
			local err = checkHealth()
			if err then
				ui:addNotice("error", err)
			end
		end)
	end,
	onNewChat = function()
		ui:clear()
		table.clear(history)
	end,
}, {
	url = serverUrl,
	token = token,
	autonomy = autonomy,
	thinking = thinkingLevel,
	mode = if useServer then "online" else "offline",
})

---------------------------------------------------------------------------
-- Lifecycle: health polling while the widget is open
---------------------------------------------------------------------------

local running = true
local lastHealth = 0

local function onEnabledChanged()
	toggleButton:SetActive(widget.Enabled)
	if widget.Enabled then
		lastHealth = os.clock()
		task.spawn(function()
			local err = checkHealth()
			if err then
				ui:addNotice("error", err)
			end
		end)
	end
end

widget:GetPropertyChangedSignal("Enabled"):Connect(onEnabledChanged)
if widget.Enabled then
	onEnabledChanged()
end

task.spawn(function()
	while running do
		task.wait(5)
		if running and widget.Enabled and os.clock() - lastHealth >= HEALTH_INTERVAL then
			lastHealth = os.clock()
			checkHealth()
		end
	end
end)

thisPlugin.Unloading:Connect(function()
	running = false
	Theme.destroy()
end)
