--!strict
-- Context: collects what the user is looking at, for POST /chat `context`.
--   selection  - Selection:Get() as {path, className, name}
--   openScript - StudioService.ActiveScript path + source, when a script tab is active
-- Sizes are capped so a request stays well below the server's 1 MiB limit.

local Selection = game:GetService("Selection")

local PathResolver = require(script.Parent.PathResolver)

local Context = {}

local MAX_SELECTION = 50
local MAX_SOURCE_CHARS = 200000

export type SelectionItem = { path: string, className: string, name: string }
export type OpenScript = { path: string, source: string }
export type Payload = { selection: { SelectionItem }, openScript: OpenScript? }

function Context.selection(): { SelectionItem }
	local items: { SelectionItem } = {}
	for _, instance in ipairs(Selection:Get()) do
		if #items >= MAX_SELECTION then
			break
		end
		local path = PathResolver.pathOf(instance)
		if path then
			table.insert(items, { path = path, className = instance.ClassName, name = instance.Name })
		end
	end
	return items
end

function Context.openScript(): OpenScript?
	local ok, active = pcall(function()
		return (game:GetService("StudioService") :: any).ActiveScript
	end)
	if not ok or typeof(active) ~= "Instance" or not active:IsA("LuaSourceContainer") then
		return nil
	end
	local path = PathResolver.pathOf(active)
	if path == nil then
		return nil
	end
	local sourceOk, source = pcall(function()
		return game:GetService("ScriptEditorService"):GetEditorSource(active)
	end)
	if not sourceOk or typeof(source) ~= "string" then
		local propertyOk, propertySource = pcall(function()
			return (active :: any).Source
		end)
		if not propertyOk or typeof(propertySource) ~= "string" then
			return nil
		end
		source = propertySource
	end
	if #source > MAX_SOURCE_CHARS then
		source = source:sub(1, MAX_SOURCE_CHARS) .. "\n-- [GODMODE: source truncated by the plugin]"
	end
	return { path = path, source = source }
end

function Context.collect(): Payload
	return { selection = Context.selection(), openScript = Context.openScript() }
end

return Context
