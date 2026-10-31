--!strict
-- Ui: small helpers for building themed Studio widgets declaratively.
-- Colours always come from Theme (painted, so they follow live theme changes).
-- Signal connections made here are on the instances themselves, so Destroy()
-- disconnects them; nothing here keeps a per-frame loop.

local Theme = require(script.Parent.Theme)

local Ui = {}

export type Props = { [string]: any }
export type ButtonStyle = "primary" | "secondary" | "normal" | "ghost" | "danger"
export type MenuItem = { id: string, label: string, description: string? }

Ui.TEXT_SIZE = 14
Ui.SMALL_TEXT = 12
Ui.BUTTON_HEIGHT = 26

-- Create an instance, assign properties (Parent last), and parent any children.
function Ui.create(className: string, props: Props?, children: { Instance }?): Instance
	local instance = Instance.new(className)
	local parent: Instance? = nil
	if props then
		for key, value in pairs(props) do
			if key == "Parent" then
				parent = value
			else
				(instance :: any)[key] = value
			end
		end
	end
	if children then
		for _, child in ipairs(children) do
			child.Parent = instance
		end
	end
	if parent then
		instance.Parent = parent
	end
	return instance
end

function Ui.corner(radius: number): Instance
	return Ui.create("UICorner", { CornerRadius = UDim.new(0, radius) })
end

-- CSS-like padding: (all), (vertical, horizontal) or (top, right, bottom, left).
function Ui.padding(top: number, right: number?, bottom: number?, left: number?): Instance
	local r = right or top
	local b = bottom or top
	local l = left or r
	return Ui.create("UIPadding", {
		PaddingTop = UDim.new(0, top),
		PaddingRight = UDim.new(0, r),
		PaddingBottom = UDim.new(0, b),
		PaddingLeft = UDim.new(0, l),
	})
end

function Ui.vlist(spacing: number, horizontalAlignment: Enum.HorizontalAlignment?): Instance
	return Ui.create("UIListLayout", {
		FillDirection = Enum.FillDirection.Vertical,
		SortOrder = Enum.SortOrder.LayoutOrder,
		HorizontalAlignment = horizontalAlignment or Enum.HorizontalAlignment.Left,
		Padding = UDim.new(0, spacing),
	})
end

function Ui.hlist(spacing: number, horizontalAlignment: Enum.HorizontalAlignment?): Instance
	return Ui.create("UIListLayout", {
		FillDirection = Enum.FillDirection.Horizontal,
		SortOrder = Enum.SortOrder.LayoutOrder,
		VerticalAlignment = Enum.VerticalAlignment.Center,
		HorizontalAlignment = horizontalAlignment or Enum.HorizontalAlignment.Left,
		Padding = UDim.new(0, spacing),
	})
end

function Ui.stroke(colorFn: () -> Color3, thickness: number?): UIStroke
	local stroke = Ui.create("UIStroke", {
		Thickness = thickness or 1,
		ApplyStrokeMode = Enum.ApplyStrokeMode.Border,
	}) :: UIStroke
	Theme.paint(stroke, "Color", colorFn)
	return stroke
end

-- Transparent full-width row that sizes to its children.
function Ui.row(spacing: number, props: Props?): Frame
	local frame = Ui.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
	}, { Ui.hlist(spacing) }) :: Frame
	if props then
		for key, value in pairs(props) do
			(frame :: any)[key] = value
		end
	end
	return frame
end

-- A wrapped, auto-height label.
function Ui.label(text: string, props: Props?): TextLabel
	local label = Ui.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Font = Theme.FONT_REGULAR,
		TextSize = Ui.TEXT_SIZE,
		TextWrapped = true,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextYAlignment = Enum.TextYAlignment.Top,
		Text = text,
	}) :: TextLabel
	if props then
		for key, value in pairs(props) do
			(label :: any)[key] = value
		end
	end
	if not (props and props.TextColor3) then
		Theme.paint(label, "TextColor3", Theme.text)
	end
	return label
end

-- Hover tint for buttons whose idle background is transparent or a theme token.
function Ui.hover(button: GuiButton, idle: () -> Color3, hovered: () -> Color3, idleTransparency: number?)
	local transparent = idleTransparency or 0
	button.MouseEnter:Connect(function()
		if button.Active then
			button.BackgroundTransparency = 0
			Theme.paint(button, "BackgroundColor3", hovered)
		end
	end)
	button.MouseLeave:Connect(function()
		button.BackgroundTransparency = transparent
		Theme.paint(button, "BackgroundColor3", idle)
	end)
end

-- A themed push button. With no width the button sizes to its text.
--   primary   accent fill (main action)
--   secondary / normal  subtle fill with hairline border
--   ghost     no fill until hovered (toolbar icons, small links)
--   danger    red fill, white text
function Ui.button(text: string, style: ButtonStyle?, width: number?): TextButton
	local kind = style or "secondary"
	local button = Ui.create("TextButton", {
		Size = UDim2.new(0, width or 0, 0, Ui.BUTTON_HEIGHT),
		AutomaticSize = if width then Enum.AutomaticSize.None else Enum.AutomaticSize.X,
		AutoButtonColor = kind ~= "ghost",
		Font = Theme.FONT_MEDIUM,
		TextSize = Ui.TEXT_SIZE,
		Text = text,
		BorderSizePixel = 0,
	}, { Ui.corner(6), Ui.padding(0, 10) }) :: TextButton
	if kind == "primary" then
		Theme.paint(button, "BackgroundColor3", Theme.accent)
		Theme.paint(button, "TextColor3", Theme.onAccent)
	elseif kind == "danger" then
		Theme.paint(button, "BackgroundColor3", Theme.danger)
		Theme.paint(button, "TextColor3", Theme.onAccent)
	elseif kind == "ghost" then
		button.BackgroundTransparency = 1
		Theme.paint(button, "BackgroundColor3", Theme.subtle)
		Theme.paint(button, "TextColor3", Theme.dimText)
		Ui.hover(button, Theme.subtle, Theme.subtle, 1)
	else
		Theme.paint(button, "BackgroundColor3", Theme.raised)
		Theme.paint(button, "TextColor3", Theme.text)
		Ui.stroke(Theme.hairline).Parent = button
	end
	return button
end

-- Square icon button (gear, plus, chevrons) using a plain Unicode glyph.
function Ui.iconButton(glyph: string, tooltip: string?): TextButton
	local button = Ui.button(glyph, "ghost", 28)
	button.Size = UDim2.fromOffset(28, 28)
	button.TextSize = 16
	button.Name = tooltip or glyph
	local padding = button:FindFirstChildOfClass("UIPadding")
	if padding then
		padding:Destroy()
	end
	return button
end

-- Small rounded pill with text, e.g. "Offline", "offline:obby", "✓ 3/3 checks".
function Ui.chip(text: string, colorFn: (() -> Color3)?): (Frame, TextLabel)
	local frame = Ui.create("Frame", {
		Size = UDim2.fromOffset(0, 20),
		AutomaticSize = Enum.AutomaticSize.X,
		BorderSizePixel = 0,
	}, { Ui.corner(10), Ui.padding(0, 8) }) :: Frame
	Theme.paint(frame, "BackgroundColor3", Theme.subtle)
	local label = Ui.create("TextLabel", {
		Name = "Text",
		BackgroundTransparency = 1,
		Size = UDim2.new(0, 0, 1, 0),
		AutomaticSize = Enum.AutomaticSize.X,
		Font = Theme.FONT_MEDIUM,
		TextSize = Ui.SMALL_TEXT,
		Text = text,
		Parent = frame,
	}) :: TextLabel
	Theme.paint(label, "TextColor3", colorFn or Theme.dimText)
	return frame, label
end

-- A themed single-line (or multi-line) text box.
function Ui.textBox(placeholder: string, multiLine: boolean?): TextBox
	local box = Ui.create("TextBox", {
		Size = UDim2.new(1, 0, 0, 28),
		Font = Theme.FONT_REGULAR,
		TextSize = Ui.TEXT_SIZE,
		Text = "",
		PlaceholderText = placeholder,
		ClearTextOnFocus = false,
		MultiLine = multiLine == true,
		TextWrapped = true,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextYAlignment = if multiLine then Enum.TextYAlignment.Top else Enum.TextYAlignment.Center,
		BorderSizePixel = 0,
	}, { Ui.corner(6), Ui.padding(4, 8) }) :: TextBox
	Theme.paint(box, "BackgroundColor3", Theme.inputBackground)
	Theme.paint(box, "TextColor3", Theme.text)
	Theme.paint(box, "PlaceholderColor3", Theme.dimText)
	local stroke = Ui.stroke(Theme.hairline)
	stroke.Parent = box
	box.Focused:Connect(function()
		Theme.paint(stroke, "Color", Theme.accent)
	end)
	box.FocusLost:Connect(function()
		Theme.paint(stroke, "Color", Theme.hairline)
	end)
	return box
end

export type CodeOptions = {
	language: string?,
	onInsert: ((code: string, language: string?) -> ())?,
}

-- An editor-like dark code panel: a header (language + Select / Insert) above a
-- read-only, selectable monospace TextBox. Plugins cannot write the OS clipboard,
-- so "Select" focuses the box and selects everything for Ctrl+C.
function Ui.codeBlock(code: string, maxLines: number?, options: CodeOptions?): Frame
	local text = code
	local hidden = 0
	if maxLines then
		local lines = string.split(code, "\n")
		if #lines > maxLines then
			text = table.concat(lines, "\n", 1, maxLines)
			hidden = #lines - maxLines
		end
	end
	local language = if options then options.language else nil

	local frame = Ui.create("Frame", {
		Name = "CodeBlock",
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		BorderSizePixel = 0,
		ClipsDescendants = true,
	}, { Ui.corner(6), Ui.vlist(0) }) :: Frame
	Theme.paint(frame, "BackgroundColor3", Theme.codeBackground)

	local header = Ui.create("Frame", {
		Name = "Header",
		Size = UDim2.new(1, 0, 0, 24),
		BorderSizePixel = 0,
		LayoutOrder = 1,
		Parent = frame,
	}, { Ui.padding(0, 4, 0, 10) }) :: Frame
	Theme.paint(header, "BackgroundColor3", Theme.codeHeader)

	local languageLabel = Ui.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, -120, 1, 0),
		Font = Theme.FONT_MONO,
		TextSize = 11,
		TextXAlignment = Enum.TextXAlignment.Left,
		Text = language or "code",
		Parent = header,
	})
	Theme.paint(languageLabel, "TextColor3", Theme.codeDimText)

	local actions = Ui.create("Frame", {
		BackgroundTransparency = 1,
		AnchorPoint = Vector2.new(1, 0),
		Position = UDim2.fromScale(1, 0),
		Size = UDim2.new(0, 120, 1, 0),
		Parent = header,
	}, { Ui.hlist(2, Enum.HorizontalAlignment.Right) })

	local function headerButton(label: string, order: number): TextButton
		local button = Ui.create("TextButton", {
			Size = UDim2.fromOffset(0, 20),
			AutomaticSize = Enum.AutomaticSize.X,
			BackgroundTransparency = 1,
			BackgroundColor3 = Color3.fromRGB(70, 70, 76),
			AutoButtonColor = false,
			Font = Theme.FONT_MEDIUM,
			TextSize = 12,
			Text = label,
			LayoutOrder = order,
			Parent = actions,
		}, { Ui.corner(4), Ui.padding(0, 6) }) :: TextButton
		Theme.paint(button, "TextColor3", Theme.codeDimText)
		button.MouseEnter:Connect(function()
			button.BackgroundTransparency = 0
		end)
		button.MouseLeave:Connect(function()
			button.BackgroundTransparency = 1
		end)
		return button
	end

	local box = Ui.create("TextBox", {
		Name = "Code",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Font = Theme.FONT_MONO,
		TextSize = 13,
		TextWrapped = true,
		MultiLine = true,
		ClearTextOnFocus = false,
		TextEditable = false,
		RichText = false,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextYAlignment = Enum.TextYAlignment.Top,
		Text = text,
		LayoutOrder = 2,
		Parent = frame,
	}, { Ui.padding(8, 10) }) :: TextBox
	Theme.paint(box, "TextColor3", Theme.codeText)

	local selectButton = headerButton("Select", 1)
	selectButton.MouseButton1Click:Connect(function()
		box:CaptureFocus()
		box.SelectionStart = 1
		box.CursorPosition = #box.Text + 1
	end)

	local onInsert = if options then options.onInsert else nil
	if onInsert then
		local insertButton = headerButton("Insert", 2)
		insertButton.MouseButton1Click:Connect(function()
			onInsert(code, language)
		end)
	end

	if hidden > 0 then
		local note = Ui.label("… " .. tostring(hidden) .. " more lines (Select copies only what is shown; Insert uses all of it)", {
			TextSize = 11,
			LayoutOrder = 3,
			TextColor3 = Theme.codeDimText(),
		})
		note.Parent = frame
		Ui.padding(0, 10, 6, 10).Parent = note
	end
	return frame
end

-- A popover menu anchored above (or below) `anchor`, inside `host` (usually the
-- widget root). A transparent full-size scrim closes it on any outside click.
-- Returns a function that closes it.
function Ui.menu(
	host: GuiObject,
	anchor: GuiObject,
	items: { MenuItem },
	selectedId: string?,
	onPick: (id: string) -> (),
	width: number?
): () -> ()
	local scrim = Ui.create("TextButton", {
		Name = "MenuScrim",
		BackgroundTransparency = 1,
		Text = "",
		AutoButtonColor = false,
		Size = UDim2.fromScale(1, 1),
		ZIndex = 50,
		Parent = host,
	}) :: TextButton

	local menuWidth = width or 220
	local relative = anchor.AbsolutePosition - host.AbsolutePosition
	local openUp = relative.Y > host.AbsoluteSize.Y / 2
	local x = math.clamp(relative.X, 4, math.max(4, host.AbsoluteSize.X - menuWidth - 4))
	local panel = Ui.create("Frame", {
		Name = "Menu",
		Size = UDim2.fromOffset(menuWidth, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		AnchorPoint = if openUp then Vector2.new(0, 1) else Vector2.new(0, 0),
		Position = if openUp
			then UDim2.fromOffset(x, relative.Y - 4)
			else UDim2.fromOffset(x, relative.Y + anchor.AbsoluteSize.Y + 4),
		BorderSizePixel = 0,
		ZIndex = 51,
		Parent = scrim,
	}, { Ui.corner(8), Ui.padding(4), Ui.vlist(0) }) :: Frame
	Theme.paint(panel, "BackgroundColor3", Theme.raised)
	Ui.stroke(Theme.hairline).Parent = panel

	local function close()
		scrim:Destroy()
	end
	scrim.MouseButton1Click:Connect(close)

	for index, item in ipairs(items) do
		local entry = Ui.create("TextButton", {
			Name = item.id,
			Size = UDim2.new(1, 0, 0, 0),
			AutomaticSize = Enum.AutomaticSize.Y,
			BackgroundTransparency = 1,
			AutoButtonColor = false,
			Text = "",
			ZIndex = 52,
			LayoutOrder = index,
			Parent = panel,
		}, { Ui.corner(6), Ui.padding(5, 8), Ui.vlist(1) }) :: TextButton
		Theme.paint(entry, "BackgroundColor3", Theme.subtle)
		Ui.hover(entry, Theme.subtle, Theme.subtle, 1)
		local selected = item.id == selectedId
		Ui.label((if selected then "✓  " else "     ") .. item.label, {
			Font = if selected then Theme.FONT_BOLD else Theme.FONT_MEDIUM,
			ZIndex = 52,
			LayoutOrder = 1,
			Parent = entry,
		})
		if item.description and item.description ~= "" then
			local hint = Ui.label("     " .. item.description, {
				TextSize = Ui.SMALL_TEXT,
				ZIndex = 52,
				LayoutOrder = 2,
				TextColor3 = Theme.dimText(),
				Parent = entry,
			})
			Theme.paint(hint, "TextColor3", Theme.dimText)
		end
		entry.MouseButton1Click:Connect(function()
			close()
			onPick(item.id)
		end)
	end
	return close
end

return Ui
