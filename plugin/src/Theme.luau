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

-- Mix two colours: t = 0 gives `a`, t = 1 gives `b`.
function Theme.mix(a: Color3, b: Color3, t: number): Color3
	return a:Lerp(b, t)
end

-- True when the current Studio theme has a dark background.
function Theme.isDark(): boolean
	local bg = Theme.background()
	return (0.299 * bg.R + 0.587 * bg.G + 0.114 * bg.B) < 0.5
end

-- Raised surface (cards, composer field): a few percent toward the text colour.
function Theme.raised(): Color3
	return Theme.mix(Theme.background(), Theme.text(), if Theme.isDark() then 0.06 else 0.035)
end

-- Subtle fill for chips, hover rows and the thinking block.
function Theme.subtle(): Color3
	return Theme.mix(Theme.background(), Theme.text(), if Theme.isDark() then 0.11 else 0.07)
end

-- Hairline separators: softer than Studio's Border colour.
function Theme.hairline(): Color3
	return Theme.mix(Theme.background(), Theme.text(), if Theme.isDark() then 0.16 else 0.13)
end

-- User bubble: the accent colour washed into the background, so MainText stays readable.
function Theme.userBubble(): Color3
	return Theme.mix(Theme.background(), Theme.accent(), if Theme.isDark() then 0.32 else 0.16)
end

function Theme.assistantBubble(): Color3
	return Theme.raised()
end

-- Code panels are always dark (editor-like) in both themes, with fixed light text.
function Theme.codeBackground(): Color3
	return if Theme.isDark() then Color3.fromRGB(24, 24, 27) else Color3.fromRGB(32, 33, 36)
end

function Theme.codeHeader(): Color3
	return if Theme.isDark() then Color3.fromRGB(36, 36, 40) else Color3.fromRGB(45, 46, 50)
end

function Theme.codeText(): Color3
	return Color3.fromRGB(220, 220, 220)
end

function Theme.codeDimText(): Color3
	return Color3.fromRGB(150, 150, 155)
end

-- Colour of `inline code` inside prose (RichText hex), readable on light and dark.
function Theme.inlineCodeHex(): string
	return if Theme.isDark() then "#E5A86E" else "#B0461C"
end

function Theme.error(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.ErrorText)
end

function Theme.warning(): Color3
	return Theme.color(Enum.StudioStyleGuideColor.WarningText)
end

function Theme.success(): Color3
	-- Studio has no "success" guide colour; a fixed mid green reads well on light and dark.
	return Color3.fromRGB(46, 160, 67)
end

function Theme.danger(): Color3
	return Color3.fromRGB(200, 40, 40)
end

function Theme.onAccent(): Color3
	return Color3.new(1, 1, 1)
end

---------------------------------------------------------------------------
-- Fonts. BuilderSans is Studio's own UI face; older Studio builds without it fall
-- back to SourceSans. RobotoMono is used for code.
---------------------------------------------------------------------------

local function pickFont(name: string, fallback: Enum.Font): Enum.Font
	local ok, font = pcall(function()
		return (Enum.Font :: any)[name]
	end)
	if ok and typeof(font) == "EnumItem" then
		return font :: any
	end
	return fallback
end

Theme.FONT_REGULAR = pickFont("BuilderSans", Enum.Font.SourceSans)
Theme.FONT_MEDIUM = pickFont("BuilderSansMedium", Enum.Font.SourceSansSemibold)
Theme.FONT_BOLD = pickFont("BuilderSansBold", Enum.Font.SourceSansBold)
Theme.FONT_MONO = Enum.Font.RobotoMono

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
