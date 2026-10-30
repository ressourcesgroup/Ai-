--!strict
-- Theme: resolves Studio theme colors and notifies listeners when the theme changes.
-- Every UI module asks Theme for colors instead of hard-coding them so the plugin
-- stays readable in both the light and dark Studio themes.

local Theme = {}

type Listener = () -> ()

local listeners: { Listener } = {}
local connection: RBXScriptConnection? = nil

local function studioTheme(): StudioTheme
	return (settings() :: any).Studio.Theme
end

-- Look up a StudioStyleGuideColor, optionally with a modifier (Hover, Pressed, Disabled, Selected).
function Theme.color(guide: Enum.StudioStyleGuideColor, modifier: Enum.StudioStyleGuideModifier?): Color3
	local ok, result = pcall(function()
		if modifier then
			return studioTheme():GetColor(guide, modifier)
		end
		return studioTheme():GetColor(guide)
	end)
	if ok then
		return result
	end
	return Color3.fromRGB(46, 46, 46)
end

-- Semantic colors used across the plugin.
function Theme.background(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.MainBackground)
end

function Theme.panel(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.Titlebar)
end

function Theme.text(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.MainText)
end

function Theme.dimText(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.DimmedText)
end

function Theme.border(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.Border)
end

function Theme.inputBackground(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.InputFieldBackground)
end

function Theme.button(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.Button)
end

function Theme.buttonText(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.ButtonText)
end

function Theme.accent(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.DialogMainButton)
end

function Theme.accentText(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.DialogMainButtonText)
end

function Theme.userBubble(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.Item, Enum.StudioStyleGuideModifier.Selected)
end

function Theme.assistantBubble(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.Item)
end

function Theme.codeBackground(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.ScriptBackground)
end

function Theme.codeText(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.ScriptText)
end

function Theme.error(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.ErrorText)
end

function Theme.warning(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.WarningText)
end

function Theme.success(): Color3
	-- Studio has no "success" guide color; ScriptBuiltInFunction is green-ish in both
	-- default themes, but a fixed mid green reads well on light and dark backgrounds.
	return Color3.fromRGB(46, 160, 67)
end

function Theme.danger(): Color3
	return Color3.fromRGB(200, 40, 40)
end

-- Register a callback that re-applies colors. It runs immediately and on every
-- Studio ThemeChanged. Returns a function that unregisters the callback (call it
-- when the themed UI is destroyed so listeners do not leak).
function Theme.bind(callback: Listener): () -> ()
	table.insert(listeners, callback)
	callback()
	if connection == nil then
		local ok, conn = pcall(function()
			return (settings() :: any).Studio.ThemeChanged:Connect(function()
				for _, listener in ipairs(table.clone(listeners)) do
					task.spawn(listener)
				end
			end)
		end)
		if ok then
			connection = conn
		end
	end
	return function()
		local index = table.find(listeners, callback)
		if index then
			table.remove(listeners, index)
		end
	end
end

-- Paint `property` of `instance` with `colorFn()` now and again whenever the Studio
-- theme changes. Registrations are weak-keyed, so destroyed UI is not kept alive.
local painted: { [Instance]: { [string]: () -> Color3 } } = setmetatable({}, { __mode = "k" }) :: any
local paintBinding: (() -> ())? = nil

local function repaintAll()
	for instance, props in pairs(painted) do
		for property, colorFn in pairs(props) do
			pcall(function()
				(instance :: any)[property] = colorFn()
			end)
		end
	end
end

function Theme.paint(instance: Instance, property: string, colorFn: () -> Color3): ()
	local props = painted[instance]
	if props == nil then
		local newProps: { [string]: () -> Color3 } = {}
		painted[instance] = newProps
		props = newProps
		instance.Destroying:Connect(function()
			painted[instance] = nil
		end)
	end
	(props :: { [string]: () -> Color3 })[property] = colorFn;
	(instance :: any)[property] = colorFn()
	if paintBinding == nil then
		paintBinding = Theme.bind(repaintAll)
	end
end

function Theme.destroy(): ()
	if connection then
		connection:Disconnect()
		connection = nil
	end
	table.clear(listeners)
	table.clear(painted)
	paintBinding = nil
end

return Theme
