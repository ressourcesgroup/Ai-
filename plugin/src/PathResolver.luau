--!strict
-- PathResolver: converts protocol dot paths ("Workspace.Map.Door") to instances and back.
--
-- Security: only services on ALLOWED_SERVICES can be addressed. Anything else
-- (CoreGui, CorePackages, PluginGuiService, RobloxReplicatedStorage, ...) is refused,
-- so a server can never make the plugin touch Studio internals or other plugins.
-- Per docs/PLUGIN_PROTOCOL.md the first segment is resolved with game:GetService and
-- every following segment with FindFirstChild. Generated names never contain dots.

local PathResolver = {}

PathResolver.ALLOWED_SERVICES = {
	Workspace = true,
	ReplicatedStorage = true,
	ReplicatedFirst = true,
	ServerScriptService = true,
	ServerStorage = true,
	StarterGui = true,
	StarterPack = true,
	StarterPlayer = true,
	Lighting = true,
	SoundService = true,
	Teams = true,
	TextChatService = true,
	MaterialService = true,
} :: { [string]: boolean }

local MAX_SEGMENTS = 64
local MAX_NAME_LENGTH = 100

-- Split and validate a path. Returns the segments or an error message.
function PathResolver.split(path: any): ({ string }?, string?)
	if typeof(path) ~= "string" or path == "" then
		return nil, "path must be a non-empty string"
	end
	local text = path :: string
	if text:sub(1, 5) == "game." then
		text = text:sub(6)
	end
	local segments = string.split(text, ".")
	if #segments > MAX_SEGMENTS then
		return nil, "path is too deep: " .. path
	end
	for _, segment in ipairs(segments) do
		if segment == "" then
			return nil, "path has an empty segment: " .. path
		end
	end
	if not PathResolver.ALLOWED_SERVICES[segments[1]] then
		return nil, "refusing path outside the allowed game services: " .. path
	end
	return segments, nil
end

-- Validate a single instance name supplied by the server.
function PathResolver.validName(name: any): (boolean, string?)
	if typeof(name) ~= "string" or name == "" then
		return false, "name must be a non-empty string"
	end
	if #(name :: string) > MAX_NAME_LENGTH then
		return false, "name is longer than " .. tostring(MAX_NAME_LENGTH) .. " characters"
	end
	if (name :: string):find(".", 1, true) then
		return false, "name must not contain '.': " .. (name :: string)
	end
	return true, nil
end

local function getService(name: string): Instance?
	local ok, service = pcall(function()
		return game:GetService(name)
	end)
	if ok then
		return service
	end
	return nil
end

-- Resolve an existing instance. Never creates anything.
function PathResolver.resolve(path: any): (Instance?, string?)
	local segments, err = PathResolver.split(path)
	if segments == nil then
		return nil, err
	end
	local current = getService(segments[1])
	if current == nil then
		return nil, "unknown service: " .. segments[1]
	end
	for i = 2, #segments do
		local child = (current :: Instance):FindFirstChild(segments[i])
		if child == nil then
			return nil, "not found: '" .. segments[i] .. "' in " .. table.concat(segments, ".", 1, i - 1)
		end
		current = child
	end
	return current, nil
end

-- Resolve a path, creating missing Folders after the service. Used only for
-- create_animation, which the protocol explicitly allows to create its folders.
function PathResolver.resolveOrCreateFolders(path: any): (Instance?, string?, { Instance })
	local created: { Instance } = {}
	local segments, err = PathResolver.split(path)
	if segments == nil then
		return nil, err, created
	end
	local current = getService(segments[1])
	if current == nil then
		return nil, "unknown service: " .. segments[1], created
	end
	for i = 2, #segments do
		local okName, nameErr = PathResolver.validName(segments[i])
		if not okName then
			return nil, nameErr, created
		end
		local child = (current :: Instance):FindFirstChild(segments[i])
		if child == nil then
			local folder = Instance.new("Folder")
			folder.Name = segments[i]
			folder.Parent = current
			table.insert(created, folder)
			child = folder
		end
		current = child
	end
	return current, nil, created
end

-- Build the protocol path of an instance (service name first), or nil when the
-- instance is not inside the DataModel.
function PathResolver.pathOf(instance: Instance): string?
	local parts: { string } = {}
	local current: Instance? = instance
	while current ~= nil and current ~= game do
		local parent: Instance? = current.Parent
		if parent == game then
			table.insert(parts, 1, current.ClassName)
		else
			table.insert(parts, 1, current.Name)
		end
		current = parent
	end
	if current ~= game or #parts == 0 then
		return nil
	end
	return table.concat(parts, ".")
end

-- True when `path` names a service itself (deleting or replacing it is refused).
function PathResolver.isServiceRoot(path: string): boolean
	local segments = PathResolver.split(path)
	return segments ~= nil and #segments == 1
end

-- Pick a name that is free under `parent` by appending _2, _3, ...
function PathResolver.uniqueName(parent: Instance, name: string): string
	if parent:FindFirstChild(name) == nil then
		return name
	end
	local index = 2
	while parent:FindFirstChild(name .. "_" .. tostring(index)) ~= nil do
		index += 1
	end
	return name .. "_" .. tostring(index)
end

return PathResolver
