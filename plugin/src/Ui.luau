--!strict
-- Ui: tiny helpers for building themed Studio widgets declaratively.

local Theme = require(script.Parent.Theme)

local Ui = {}

export type Props = { [string]: any }

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

function Ui.padding(all: number): Instance
	return Ui.create("UIPadding", {
		PaddingTop = UDim.new(0, all),
		PaddingBottom = UDim.new(0, all),
		PaddingLeft = UDim.new(0, all),
		PaddingRight = UDim.new(0, all),
	})
end

function Ui.vlist(spacing: number): Instance
	return Ui.create("UIListLayout", {
		FillDirection = Enum.FillDirection.Vertical,
		SortOrder = Enum.SortOrder.LayoutOrder,
		Padding = UDim.new(0, spacing),
	})
end

function Ui.hlist(spacing: number): Instance
	return Ui.create("UIListLayout", {
		FillDirection = Enum.FillDirection.Horizontal,
		SortOrder = Enum.SortOrder.LayoutOrder,
		VerticalAlignment = Enum.VerticalAlignment.Center,
		Padding = UDim.new(0, spacing),
	})
end

function Ui.stroke(colorFn: () -> Color3): Instance
	local stroke = Ui.create("UIStroke", { Thickness = 1, ApplyStrokeMode = Enum.ApplyStrokeMode.Border })
	Theme.paint(stroke, "Color", colorFn)
	return stroke
end

-- A wrapped, auto-height label.
function Ui.label(text: string, props: Props?): TextLabel
	local label = Ui.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Font = Enum.Font.SourceSans,
		TextSize = 15,
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

-- A themed push button. `primary` uses the dialog accent; `danger` uses red.
function Ui.button(text: string, style: ("normal" | "primary" | "danger")?, width: number?): TextButton
	local button = Ui.create("TextButton", {
		Size = UDim2.new(0, width or 80, 0, 26),
		AutoButtonColor = true,
		Font = Enum.Font.SourceSansSemibold,
		TextSize = 15,
		Text = text,
		BorderSizePixel = 0,
	}, { Ui.corner(4) }) :: TextButton
	if style == "primary" then
		Theme.paint(button, "BackgroundColor3", Theme.accent)
		Theme.paint(button, "TextColor3", Theme.accentText)
	elseif style == "danger" then
		Theme.paint(button, "BackgroundColor3", Theme.danger)
		button.TextColor3 = Color3.new(1, 1, 1)
	else
		Theme.paint(button, "BackgroundColor3", Theme.button)
		Theme.paint(button, "TextColor3", Theme.buttonText)
	end
	return button
end

-- A themed single-line (or multi-line) text box.
function Ui.textBox(placeholder: string, multiLine: boolean?): TextBox
	local box = Ui.create("TextBox", {
		Size = UDim2.new(1, 0, 0, 26),
		Font = Enum.Font.SourceSans,
		TextSize = 15,
		Text = "",
		PlaceholderText = placeholder,
		ClearTextOnFocus = false,
		MultiLine = multiLine == true,
		TextWrapped = true,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextYAlignment = if multiLine then Enum.TextYAlignment.Top else Enum.TextYAlignment.Center,
		BorderSizePixel = 0,
	}, { Ui.corner(4), Ui.padding(4) }) :: TextBox
	Theme.paint(box, "BackgroundColor3", Theme.inputBackground)
	Theme.paint(box, "TextColor3", Theme.text)
	Theme.paint(box, "PlaceholderColor3", Theme.dimText)
	Ui.stroke(Theme.border).Parent = box
	return box
end

-- A read-only, selectable monospace block (users can select and copy the text).
function Ui.codeBlock(code: string, maxLines: number?): Frame
	local text = code
	local truncated = false
	if maxLines then
		local lines = string.split(code, "\n")
		if #lines > maxLines then
			text = table.concat(lines, "\n", 1, maxLines)
			truncated = true
		end
	end
	local frame = Ui.create("Frame", {
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		BorderSizePixel = 0,
	}, { Ui.corner(4), Ui.padding(6), Ui.vlist(2) }) :: Frame
	Theme.paint(frame, "BackgroundColor3", Theme.codeBackground)
	local box = Ui.create("TextBox", {
		Name = "Code",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Font = Enum.Font.RobotoMono,
		TextSize = 13,
		TextWrapped = true,
		MultiLine = true,
		ClearTextOnFocus = false,
		TextEditable = false,
		RichText = false,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextYAlignment = Enum.TextYAlignment.Top,
		Text = text,
		LayoutOrder = 1,
		Parent = frame,
	})
	Theme.paint(box, "TextColor3", Theme.codeText)
	if truncated then
		local note = Ui.label("... (" .. tostring(#string.split(code, "\n") - (maxLines :: number)) .. " more lines)", {
			TextSize = 13,
			LayoutOrder = 2,
			TextColor3 = Theme.dimText(),
		})
		Theme.paint(note, "TextColor3", Theme.dimText)
		note.Parent = frame
	end
	return frame
end

return Ui
