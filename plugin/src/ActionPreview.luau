--!strict
-- ActionPreview: one card per proposed action, with Apply / Discard buttons.
--
-- Cards show the action type, target path, a summary (script preview, diff,
-- instance outline, animation summary) and the server's verification results.
-- Destructive actions are shown with a red warning and need a second, explicit
-- "Confirm delete" click. Nothing is applied without a user click.

local Theme = require(script.Parent.Theme)
local Ui = require(script.Parent.Ui)
local PathResolver = require(script.Parent.PathResolver)
local ActionApplier = require(script.Parent.ActionApplier)

local ActionPreview = {}
ActionPreview.__index = ActionPreview

local PREVIEW_LINES = 20
local DIFF_LINES = 40
local OUTLINE_LINES = 30

export type CardState = "pending" | "applying" | "applied" | "failed" | "discarded"

export type Options = {
	verification: { any },
	onApply: (action: any) -> ActionApplier.Result,
	onVerify: ((source: string) -> (boolean, string))?,
	onResult: (action: any, result: ActionApplier.Result) -> (),
}

type CardData = {
	action: any,
	frame: Frame,
	buttons: Frame,
	status: TextLabel,
	state: CardState,
	options: Options,
}

export type Card = typeof(setmetatable({} :: CardData, ActionPreview))

local TYPE_TITLES = {
	create_script = "Create script",
	edit_script = "Edit script",
	create_instances = "Create instances",
	create_animation = "Create animation",
	delete_instance = "Delete instance",
} :: { [string]: string }

---------------------------------------------------------------------------
-- Summaries
---------------------------------------------------------------------------

-- Line diff by trimming the common prefix and suffix; good enough to show what an
-- edit_script replaces without a full LCS.
function ActionPreview.diff(oldSource: string, newSource: string, maxLines: number): (string, number, number)
	local old = string.split(oldSource, "\n")
	local new = string.split(newSource, "\n")
	local prefix = 0
	while prefix < #old and prefix < #new and old[prefix + 1] == new[prefix + 1] do
		prefix += 1
	end
	local suffix = 0
	while suffix < #old - prefix and suffix < #new - prefix and old[#old - suffix] == new[#new - suffix] do
		suffix += 1
	end
	local lines: { string } = {}
	local removed, added = 0, 0
	table.insert(lines, "@@ line " .. tostring(prefix + 1) .. " @@")
	for i = prefix + 1, #old - suffix do
		removed += 1
		table.insert(lines, "- " .. old[i])
	end
	for i = prefix + 1, #new - suffix do
		added += 1
		table.insert(lines, "+ " .. new[i])
	end
	if removed == 0 and added == 0 then
		return "(no changes)", 0, 0
	end
	if #lines > maxLines then
		local extra = #lines - maxLines
		local kept = table.move(lines, 1, maxLines, 1, {})
		table.insert(kept, "... (" .. tostring(extra) .. " more diff lines)")
		lines = kept
	end
	return table.concat(lines, "\n"), removed, added
end

local function outline(node: any, depth: number, lines: { string }, counter: { number })
	if typeof(node) ~= "table" then
		return
	end
	counter[1] += 1
	if #lines < OUTLINE_LINES then
		table.insert(lines, string.rep("  ", depth) .. tostring(node.className) .. " \"" .. tostring(node.name) .. "\"")
	end
	if typeof(node.children) == "table" then
		for _, child in ipairs(node.children) do
			outline(child, depth + 1, lines, counter)
		end
	end
end

local function currentSource(path: any): string?
	local target = PathResolver.resolve(path)
	if target == nil or not target:IsA("LuaSourceContainer") then
		return nil
	end
	local ok, source = pcall(function()
		return game:GetService("ScriptEditorService"):GetEditorSource(target)
	end)
	if ok and typeof(source) == "string" then
		return source
	end
	return (target :: any).Source
end

-- Pick the verification entries that concern this action. Check labels look like
-- "structure:<label>", "lint:<label>", "animation:<name>".
function ActionPreview.relevantChecks(action: any, verification: { any }): { any }
	local target = ActionApplier.targetPath(action)
	local name = if typeof(action.name) == "string" then action.name else nil
	local out = {}
	for _, entry in ipairs(verification) do
		if typeof(entry) == "table" and typeof(entry.check) == "string" then
			local label = entry.check:match("^[^:]*:(.+)$")
			if label ~= nil and (label == target or label == name or target:sub(-(#label + 1)) == "." .. label) then
				table.insert(out, entry)
			end
		end
	end
	return out
end

---------------------------------------------------------------------------
-- Card
---------------------------------------------------------------------------

local function addLabel(parent: Instance, text: string, order: number, colorFn: (() -> Color3)?, props: Ui.Props?): TextLabel
	local label = Ui.label(text, props)
	label.LayoutOrder = order
	if colorFn then
		Theme.paint(label, "TextColor3", colorFn)
	end
	label.Parent = parent
	return label
end

-- A "✓ n/m checks" badge; clicking it shows / hides the individual checks.
local function addVerification(parent: Instance, checks: { any }, order: number)
	if #checks == 0 then
		return
	end
	local lines: { string } = {}
	local passed = 0
	for _, entry in ipairs(checks) do
		local ok = entry.ok == true
		if ok then
			passed += 1
		end
		local detail = if typeof(entry.detail) == "string" and entry.detail ~= "" then " - " .. entry.detail else ""
		table.insert(lines, (if ok then "PASS " else "FAIL ") .. entry.check .. detail)
	end
	local allOk = passed == #checks
	local colorFn = if allOk then Theme.success else Theme.error
	local row = Ui.row(6, { LayoutOrder = order, Name = "Verification" })
	row.Parent = parent
	local badge = Ui.create("TextButton", {
		Size = UDim2.fromOffset(0, 20),
		AutomaticSize = Enum.AutomaticSize.X,
		AutoButtonColor = false,
		BorderSizePixel = 0,
		Font = Theme.FONT_MEDIUM,
		TextSize = Ui.SMALL_TEXT,
		Text = (if allOk then "✓ " else "✕ ") .. tostring(passed) .. "/" .. tostring(#checks) .. " checks  ▸",
		Parent = row,
	}, { Ui.corner(10), Ui.padding(0, 8) }) :: TextButton
	Theme.paint(badge, "BackgroundColor3", Theme.subtle)
	Theme.paint(badge, "TextColor3", colorFn)
	local details = addLabel(parent, table.concat(lines, "\n"), order + 1, colorFn, {
		TextSize = 12,
		Font = Theme.FONT_MONO,
		Visible = false,
	})
	badge.MouseButton1Click:Connect(function()
		details.Visible = not details.Visible
		badge.Text = (if allOk then "✓ " else "✕ ") .. tostring(passed) .. "/" .. tostring(#checks)
			.. " checks  " .. (if details.Visible then "▾" else "▸")
	end)
end

local function describe(card: CardData, body: Frame)
	local action = card.action
	local kind = action.type
	local order = 10
	if kind == "create_script" then
		addLabel(body, tostring(action.class) .. " in " .. tostring(action.parent_path), order, Theme.dimText, { TextSize = 13 })
		if typeof(action.source) == "string" then
			local block = Ui.codeBlock(action.source, PREVIEW_LINES)
			block.LayoutOrder = order + 1
			block.Parent = body
		end
	elseif kind == "edit_script" then
		local old = currentSource(action.path)
		if old == nil then
			addLabel(body, "Warning: " .. tostring(action.path) .. " was not found or is not a script; applying will fail.", order, Theme.warning)
		elseif typeof(action.new_source) == "string" then
			local text, removed, added = ActionPreview.diff(old, action.new_source, DIFF_LINES)
			addLabel(body, "Diff: -" .. tostring(removed) .. " / +" .. tostring(added) .. " lines", order, Theme.dimText, { TextSize = 13 })
			local block = Ui.codeBlock(text, nil)
			block.LayoutOrder = order + 1
			block.Parent = body
		end
	elseif kind == "create_instances" then
		local lines: { string } = {}
		local counter = { 0 }
		outline(action.tree, 0, lines, counter)
		if counter[1] > #lines then
			table.insert(lines, "... (" .. tostring(counter[1] - #lines) .. " more)")
		end
		addLabel(body, tostring(counter[1]) .. " instance(s) under " .. tostring(action.parent_path), order, Theme.dimText, { TextSize = 13 })
		local block = Ui.codeBlock(table.concat(lines, "\n"), nil)
		block.LayoutOrder = order + 1
		block.Parent = body
	elseif kind == "create_animation" then
		local keyframeCount = if typeof(action.keyframes) == "table" then #action.keyframes else 0
		local parts: { [string]: boolean } = {}
		local partCount = 0
		if typeof(action.keyframes) == "table" then
			for _, frame in ipairs(action.keyframes) do
				if typeof(frame) == "table" and typeof(frame.poses) == "table" then
					for part in pairs(frame.poses) do
						if not parts[part] then
							parts[part] = true
							partCount += 1
						end
					end
				end
			end
		end
		local summary = string.format(
			"%s rig, %d keyframes, %s s, looped=%s, priority=%s, %d posed parts",
			tostring(action.rig or "R15"),
			keyframeCount,
			tostring(action.duration or "?"),
			tostring(action.looped == true),
			tostring(action.priority or "Core"),
			partCount
		)
		addLabel(body, summary, order, Theme.dimText, { TextSize = 13 })
		if typeof(action.provenance) == "string" then
			addLabel(body, "Provenance: " .. action.provenance, order + 1, Theme.dimText, { TextSize = 13 })
		end
	elseif kind == "delete_instance" then
		local target = PathResolver.resolve(action.path)
		local detail = if target
			then target.ClassName .. " with " .. tostring(#target:GetDescendants()) .. " descendant(s)"
			else "target not found"
		addLabel(body, "DESTRUCTIVE: deletes " .. tostring(action.path) .. " (" .. detail .. "). Undo (Ctrl+Z) can restore it.", order, Theme.danger, {
			Font = Theme.FONT_BOLD,
		})
	else
		addLabel(body, "Unsupported action type '" .. tostring(kind) .. "'; it cannot be applied.", order, Theme.error)
	end
end

function ActionPreview.new(action: any, options: Options): Card
	local destructive = ActionApplier.isDestructive(action)
	local frame = Ui.create("Frame", {
		Name = "ActionCard",
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		BorderSizePixel = 0,
	}, { Ui.corner(8), Ui.padding(10), Ui.vlist(6) }) :: Frame
	Theme.paint(frame, "BackgroundColor3", Theme.raised)
	local stroke = Ui.stroke(if destructive then Theme.danger else Theme.hairline)
	if destructive then
		(stroke :: UIStroke).Thickness = 2
	end
	stroke.Parent = frame

	local kind = if typeof(action) == "table" then action.type else nil
	-- Header: type pill, then the title (action name or target), then the
	-- description and the full target path in monospace.
	local pillRow = Ui.row(6, { LayoutOrder = 1 })
	pillRow.Parent = frame
	local pill = Ui.chip(string.upper(TYPE_TITLES[kind] or tostring(kind)), if destructive then Theme.danger else Theme.accent)
	pill.Parent = pillRow
	local target = ActionApplier.targetPath(action)
	local name = if typeof(action) == "table" and typeof(action.name) == "string" then action.name else target:match("([^%.]+)$")
	addLabel(frame, name or target, 2, nil, { Font = Theme.FONT_BOLD, TextSize = 15 })
	if typeof(action) == "table" and typeof(action.description) == "string" then
		addLabel(frame, action.description, 3, nil, { TextSize = 14 })
	end
	addLabel(frame, target, 4, Theme.dimText, { TextSize = 12, Font = Theme.FONT_MONO })

	local buttons = Ui.create("Frame", {
		Name = "Buttons",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 26),
		LayoutOrder = 100,
	}, { Ui.hlist(6) }) :: Frame

	local status = addLabel(frame, "", 101, Theme.dimText, { TextSize = 13, Visible = false })

	local self = setmetatable({
		action = action,
		frame = frame,
		buttons = buttons,
		status = status,
		state = "pending",
		options = options,
	} :: CardData, ActionPreview)

	if typeof(action) == "table" then
		describe(self, frame)
		addVerification(frame, ActionPreview.relevantChecks(action, options.verification), 50)
	end
	buttons.Parent = frame
	self:showDefaultButtons()
	return self
end

function ActionPreview.clearButtons(self: Card)
	for _, child in ipairs(self.buttons:GetChildren()) do
		if child:IsA("GuiButton") then
			child:Destroy()
		end
	end
end

function ActionPreview.setStatus(self: Card, text: string, colorFn: () -> Color3)
	self.status.Text = text
	self.status.Visible = true
	Theme.paint(self.status, "TextColor3", colorFn)
end

function ActionPreview.showDefaultButtons(self: Card)
	self:clearButtons()
	local action = self.action
	local supported = typeof(action) == "table" and table.find(ActionApplier.SUPPORTED_ACTIONS, action.type) ~= nil
	if not supported then
		self.state = "discarded"
		self:setStatus("Skipped: unsupported action.", Theme.warning)
		return
	end

	if ActionApplier.isDestructive(action) then
		local deleteButton = Ui.button("Delete…", "danger")
		deleteButton.LayoutOrder = 1
		deleteButton.Parent = self.buttons
		deleteButton.MouseButton1Click:Connect(function()
			self:showConfirmButtons()
		end)
	else
		local applyButton = Ui.button("Apply", "primary")
		applyButton.LayoutOrder = 1
		applyButton.Parent = self.buttons
		applyButton.MouseButton1Click:Connect(function()
			self:apply()
		end)
	end

	local discardButton = Ui.button("Discard", "secondary")
	discardButton.LayoutOrder = 2
	discardButton.Parent = self.buttons
	discardButton.MouseButton1Click:Connect(function()
		self.state = "discarded"
		self:clearButtons()
		self:setStatus("Discarded. Nothing was changed.", Theme.dimText)
	end)

	local source = if action.type == "create_script" then action.source elseif action.type == "edit_script" then action.new_source else nil
	local onVerify = self.options.onVerify
	if typeof(source) == "string" and onVerify then
		local verifyButton = Ui.button("Re-verify", "ghost")
		verifyButton.LayoutOrder = 3
		verifyButton.Parent = self.buttons
		verifyButton.MouseButton1Click:Connect(function()
			self:setStatus("Verifying...", Theme.dimText)
			task.spawn(function()
				local passed, summary = onVerify(source)
				self:setStatus(summary, if passed then Theme.success else Theme.warning)
			end)
		end)
	end
end

function ActionPreview.showConfirmButtons(self: Card)
	self:clearButtons()
	self:setStatus("This permanently removes " .. ActionApplier.targetPath(self.action) .. " from the place (Undo can restore it). Are you sure?", Theme.danger)
	local confirmButton = Ui.button("Confirm delete", "danger")
	confirmButton.LayoutOrder = 1
	confirmButton.Parent = self.buttons
	confirmButton.MouseButton1Click:Connect(function()
		self:apply()
	end)
	local cancelButton = Ui.button("Cancel", "secondary")
	cancelButton.LayoutOrder = 2
	cancelButton.Parent = self.buttons
	cancelButton.MouseButton1Click:Connect(function()
		self.status.Visible = false
		self:showDefaultButtons()
	end)
end

function ActionPreview.isPending(self: Card): boolean
	return self.state == "pending"
end

-- True when this card may be included in a one-confirmation batch (autonomy 4-5).
function ActionPreview.isBatchable(self: Card): boolean
	local action = self.action
	return self.state == "pending"
		and typeof(action) == "table"
		and action.requires_approval == false
		and not ActionApplier.isDestructive(action)
		and table.find(ActionApplier.SUPPORTED_ACTIONS, action.type) ~= nil
end

-- Apply this card's action and show the real outcome. Returns the result.
function ActionPreview.apply(self: Card): ActionApplier.Result?
	if self.state ~= "pending" then
		return nil
	end
	self.state = "applying"
	self:clearButtons()
	self:setStatus("Applying...", Theme.dimText)
	local result = self.options.onApply(self.action)
	if result.ok then
		self.state = "applied"
		self:setStatus("Applied: " .. result.message, Theme.success)
	else
		self.state = "failed"
		self:setStatus("FAILED: " .. result.message, Theme.error)
	end
	self.options.onResult(self.action, result)
	return result
end

return ActionPreview
