--!strict
-- ActionApplier: applies the declarative actions from docs/PLUGIN_PROTOCOL.md.
--
-- * Each action is applied inside ChangeHistoryService:TryBeginRecording /
--   FinishRecording, so it is exactly one undo step. Success => Commit;
--   any error => Cancel (Studio reverts everything done during the recording).
-- * Nothing received from the server is ever executed. Scripts are created with
--   their source as plain text; instances are built from a fixed allow-list of
--   value types (PropertyDecoder); paths are limited to normal game services
--   (PathResolver).
-- * Every call returns a Result describing exactly what happened. Callers must
--   report `ok = false` results as failures, never as success.

local ChangeHistoryService = game:GetService("ChangeHistoryService")
local CollectionService = game:GetService("CollectionService")
local Selection = game:GetService("Selection")

local PathResolver = require(script.Parent.PathResolver)
local PropertyDecoder = require(script.Parent.PropertyDecoder)

local ActionApplier = {}

export type Result = {
	ok: boolean,
	message: string,
	created: Instance?,
	previewId: string?,
}

ActionApplier.SUPPORTED_ACTIONS = {
	"create_script",
	"edit_script",
	"create_instances",
	"create_animation",
	"delete_instance",
}

local SCRIPT_CLASSES = { Script = true, LocalScript = true, ModuleScript = true } :: { [string]: boolean }
local EASING_STYLES = {
	Linear = true,
	Constant = true,
	Cubic = true,
	Elastic = true,
	Bounce = true,
	CubicV2 = true,
} :: { [string]: boolean }
local EASING_DIRECTIONS = { In = true, Out = true, InOut = true } :: { [string]: boolean }
local ANIMATION_ROOT = "HumanoidRootPart"
local DEFAULT_ANIMATION_PARENT = "ServerStorage.GodmodeAnimations"
local MAX_TREE_NODES = 5000
local MAX_TREE_DEPTH = 50
local MAX_SOURCE_LENGTH = 1000000

-- Properties set first / last when building instances. JSONDecode does not keep
-- object key order, so the protocol's "Size before CFrame" rule is enforced here.
local FIRST_PROPERTIES = { "Shape", "Size" }
local LAST_PROPERTIES = { "CFrame", "Position", "Orientation", "Rotation", "PivotOffset" }

local function fail(message: string): Result
	return { ok = false, message = message, created = nil, previewId = nil }
end

local function succeed(message: string, created: Instance?, previewId: string?): Result
	return { ok = true, message = message, created = created, previewId = previewId }
end

-- Destructive actions always require an explicit confirmation in the UI.
function ActionApplier.isDestructive(action: any): boolean
	return typeof(action) == "table" and (action.type == "delete_instance" or action.destructive == true)
end

-- Best-effort target path for display.
function ActionApplier.targetPath(action: any): string
	if typeof(action) ~= "table" then
		return "?"
	end
	if typeof(action.path) == "string" then
		return action.path
	end
	local parentPath = action.parent_path
	if action.type == "create_animation" and parentPath == nil then
		parentPath = DEFAULT_ANIMATION_PARENT
	end
	local name = action.name
	if action.type == "create_instances" and typeof(action.tree) == "table" then
		name = action.tree.name
	end
	if typeof(parentPath) == "string" and typeof(name) == "string" then
		return parentPath .. "." .. name
	end
	if typeof(parentPath) == "string" then
		return parentPath
	end
	return "?"
end

---------------------------------------------------------------------------
-- Script source helpers
---------------------------------------------------------------------------

local function readSource(container: LuaSourceContainer): string
	local ok, source = pcall(function()
		return game:GetService("ScriptEditorService"):GetEditorSource(container)
	end)
	if ok and typeof(source) == "string" then
		return source
	end
	return (container :: any).Source
end

-- Write source through ScriptEditorService when available (keeps open editor
-- tabs in sync), falling back to the Source property. Returns an error or nil.
local function writeSource(container: LuaSourceContainer, source: string): string?
	local editorOk, editorErr = pcall(function()
		local ScriptEditorService = game:GetService("ScriptEditorService")
		ScriptEditorService:UpdateSourceAsync(container, function(_old: string): string
			return source
		end)
	end)
	if not editorOk then
		local propertyOk, propertyErr = pcall(function()
			(container :: any).Source = source
		end)
		if not propertyOk then
			return "could not set source (ScriptEditorService: " .. tostring(editorErr) .. "; Source: " .. tostring(propertyErr) .. ")"
		end
	end
	if readSource(container) ~= source then
		return "source did not update (the script may be locked by Team Create or another editor)"
	end
	return nil
end

local function checkSource(source: any): string?
	if typeof(source) ~= "string" then
		return "source must be a string"
	end
	if #source > MAX_SOURCE_LENGTH then
		return "source is too large"
	end
	return nil
end

---------------------------------------------------------------------------
-- Handlers. Each returns a Result; errors raised inside are caught by apply().
---------------------------------------------------------------------------

local function createScript(action: any): Result
	local className = action.class
	if typeof(className) ~= "string" or not SCRIPT_CLASSES[className] then
		return fail("class must be Script, LocalScript or ModuleScript (got " .. tostring(className) .. ")")
	end
	local okName, nameErr = PathResolver.validName(action.name)
	if not okName then
		return fail(nameErr :: string)
	end
	local sourceErr = checkSource(action.source)
	if sourceErr then
		return fail(sourceErr)
	end
	local parent, err = PathResolver.resolve(action.parent_path)
	if parent == nil then
		return fail("parent_path: " .. tostring(err))
	end

	local name = PathResolver.uniqueName(parent, action.name)
	local container = Instance.new(className) :: LuaSourceContainer
	container.Name = name
	container.Parent = parent
	local writeErr = writeSource(container, action.source)
	if writeErr then
		container:Destroy()
		return fail(writeErr)
	end
	local note = if name ~= action.name then " (renamed: '" .. action.name .. "' already existed)" else ""
	return succeed(
		"created " .. className .. " " .. (PathResolver.pathOf(container) or name) .. note,
		container
	)
end

local function editScript(action: any): Result
	local sourceErr = checkSource(action.new_source)
	if sourceErr then
		return fail("new_source: " .. sourceErr)
	end
	local target, err = PathResolver.resolve(action.path)
	if target == nil then
		return fail("path: " .. tostring(err))
	end
	if not SCRIPT_CLASSES[target.ClassName] then
		return fail(tostring(action.path) .. " is a " .. target.ClassName .. ", not a script")
	end
	local container = target :: LuaSourceContainer
	local writeErr = writeSource(container, action.new_source)
	if writeErr then
		return fail(writeErr)
	end
	return succeed("replaced the source of " .. tostring(action.path), container)
end

local function orderedPropertyNames(properties: { [string]: any }): { string }
	local first: { string } = {}
	local middle: { string } = {}
	local last: { string } = {}
	for _, name in ipairs(FIRST_PROPERTIES) do
		if properties[name] ~= nil then
			table.insert(first, name)
		end
	end
	for name in pairs(properties) do
		if typeof(name) == "string" and not table.find(FIRST_PROPERTIES, name) and not table.find(LAST_PROPERTIES, name) then
			table.insert(middle, name)
		end
	end
	table.sort(middle)
	for _, name in ipairs(LAST_PROPERTIES) do
		if properties[name] ~= nil then
			table.insert(last, name)
		end
	end
	local ordered = first
	for _, name in ipairs(middle) do
		table.insert(ordered, name)
	end
	for _, name in ipairs(last) do
		table.insert(ordered, name)
	end
	return ordered
end

type BuildState = { count: number }

-- Build an unparented instance tree. Returns the root or an error.
local function buildNode(node: any, depth: number, state: BuildState, path: string): (Instance?, string?)
	if typeof(node) ~= "table" then
		return nil, path .. ": node must be an object"
	end
	if depth > MAX_TREE_DEPTH then
		return nil, path .. ": tree is deeper than " .. tostring(MAX_TREE_DEPTH)
	end
	state.count += 1
	if state.count > MAX_TREE_NODES then
		return nil, "tree has more than " .. tostring(MAX_TREE_NODES) .. " nodes"
	end
	if typeof(node.className) ~= "string" then
		return nil, path .. ": className is required"
	end
	local okName, nameErr = PathResolver.validName(node.name)
	if not okName then
		return nil, path .. ": " .. tostring(nameErr)
	end
	local nodePath = path .. "." .. node.name

	local created, instanceOrErr = pcall(function()
		return Instance.new(node.className)
	end)
	if not created then
		return nil, nodePath .. ": cannot create class '" .. node.className .. "' (" .. tostring(instanceOrErr) .. ")"
	end
	local instance = instanceOrErr :: Instance
	instance.Name = node.name

	local function abort(message: string): (Instance?, string?)
		instance:Destroy()
		return nil, message
	end

	if node.properties ~= nil then
		if typeof(node.properties) ~= "table" then
			return abort(nodePath .. ": properties must be an object")
		end
		for _, property in ipairs(orderedPropertyNames(node.properties)) do
			local propErr = PropertyDecoder.apply(instance, property, node.properties[property])
			if propErr then
				return abort(nodePath .. ": " .. propErr)
			end
		end
	end

	if node.attributes ~= nil then
		if typeof(node.attributes) ~= "table" then
			return abort(nodePath .. ": attributes must be an object")
		end
		for attrName, attrValue in pairs(node.attributes) do
			local attrErr = PropertyDecoder.applyAttribute(instance, attrName, attrValue)
			if attrErr then
				return abort(nodePath .. ": " .. attrErr)
			end
		end
	end

	if node.tags ~= nil then
		if typeof(node.tags) ~= "table" then
			return abort(nodePath .. ": tags must be an array of strings")
		end
		for _, tag in ipairs(node.tags) do
			if typeof(tag) ~= "string" or tag == "" then
				return abort(nodePath .. ": tags must be non-empty strings")
			end
			CollectionService:AddTag(instance, tag)
		end
	end

	if node.children ~= nil then
		if typeof(node.children) ~= "table" then
			return abort(nodePath .. ": children must be an array")
		end
		for _, childNode in ipairs(node.children) do
			local child, childErr = buildNode(childNode, depth + 1, state, nodePath)
			if child == nil then
				return abort(tostring(childErr))
			end
			child.Parent = instance
		end
	end

	return instance, nil
end

local function createInstances(action: any): Result
	local parent, err = PathResolver.resolve(action.parent_path)
	if parent == nil then
		return fail("parent_path: " .. tostring(err))
	end
	local state: BuildState = { count = 0 }
	local root, buildErr = buildNode(action.tree, 1, state, tostring(action.parent_path))
	if root == nil then
		return fail(tostring(buildErr))
	end
	local requested = root.Name
	root.Name = PathResolver.uniqueName(parent, requested)
	root.Parent = parent -- Parent is always set last.
	local note = if root.Name ~= requested then " (renamed: '" .. requested .. "' already existed)" else ""
	return succeed(
		"created " .. root.ClassName .. " " .. (PathResolver.pathOf(root) or root.Name)
			.. " with " .. tostring(state.count) .. " instance(s)" .. note,
		root
	)
end

---------------------------------------------------------------------------
-- Animation
---------------------------------------------------------------------------

local function vector3Of(value: any, label: string): (Vector3?, string?)
	if value == nil then
		return Vector3.zero, nil
	end
	if typeof(value) ~= "table" or #value ~= 3 then
		return nil, label .. " must be [x, y, z]"
	end
	for i = 1, 3 do
		if typeof(value[i]) ~= "number" or value[i] ~= value[i] or math.abs(value[i]) > 1e6 then
			return nil, label .. "[" .. tostring(i) .. "] is not a finite number"
		end
	end
	return Vector3.new(value[1], value[2], value[3]), nil
end

-- Pose CFrame per protocol: CFrame.new(position) * CFrame.Angles(rad X, rad Y, rad Z)
-- (X, then Y, then Z), in the joint's Motor6D space.
local function poseCFrame(pose: any, label: string): (CFrame?, string?)
	local rotation, rotErr = vector3Of(pose.rotation, label .. ".rotation")
	if rotation == nil then
		return nil, rotErr
	end
	local position, posErr = vector3Of(pose.position, label .. ".position")
	if position == nil then
		return nil, posErr
	end
	return CFrame.new(position.X, position.Y, position.Z)
		* CFrame.Angles(math.rad(rotation.X), math.rad(rotation.Y), math.rad(rotation.Z)), nil
end

local function easingStyle(name: any, fallback: Enum.PoseEasingStyle): (Enum.PoseEasingStyle?, string?)
	if name == nil then
		return fallback, nil
	end
	if typeof(name) ~= "string" or not EASING_STYLES[name] then
		return nil, "unsupported easing_style: " .. tostring(name)
	end
	return (Enum.PoseEasingStyle :: any)[name], nil
end

local function easingDirection(name: any, fallback: Enum.PoseEasingDirection): (Enum.PoseEasingDirection?, string?)
	if name == nil then
		return fallback, nil
	end
	if typeof(name) ~= "string" or not EASING_DIRECTIONS[name] then
		return nil, "unsupported easing_direction: " .. tostring(name)
	end
	return (Enum.PoseEasingDirection :: any)[name], nil
end

-- A part is a hierarchy root when it has no parent entry. JSON `null` values vanish
-- in JSONDecode, so `{"HumanoidRootPart": null}` and a missing key look the same;
-- only HumanoidRootPart or a part that is itself used as a parent may be a root.
local function isRootPart(part: string, hierarchy: { [string]: any }): boolean
	if hierarchy[part] ~= nil then
		return false
	end
	if part == ANIMATION_ROOT then
		return true
	end
	for _, parentName in pairs(hierarchy) do
		if parentName == part then
			return true
		end
	end
	return false
end

local function buildKeyframe(frame: any, index: number, hierarchy: { [string]: any }): (Keyframe?, string?)
	local label = "keyframes[" .. tostring(index) .. "]"
	if typeof(frame) ~= "table" then
		return nil, label .. " must be an object"
	end
	if typeof(frame.time) ~= "number" or frame.time ~= frame.time or frame.time < 0 or frame.time > 3600 then
		return nil, label .. ".time must be a number >= 0"
	end
	local defaultStyle, styleErr = easingStyle(frame.easing_style, Enum.PoseEasingStyle.Linear)
	if defaultStyle == nil then
		return nil, label .. ": " .. tostring(styleErr)
	end
	local defaultDirection, dirErr = easingDirection(frame.easing_direction, Enum.PoseEasingDirection.In)
	if defaultDirection == nil then
		return nil, label .. ": " .. tostring(dirErr)
	end
	if typeof(frame.poses) ~= "table" then
		return nil, label .. ".poses must be an object"
	end

	local keyframe = Instance.new("Keyframe")
	keyframe.Time = frame.time
	keyframe.Name = if typeof(frame.name) == "string" and frame.name ~= "" then frame.name else "Keyframe"

	local poses: { [string]: Pose } = {}

	local function abort(message: string): (Keyframe?, string?)
		keyframe:Destroy()
		return nil, message
	end

	-- Create (or fetch) the Pose for `part`, creating its ancestors first.
	local function ensurePose(part: string, depth: number): (Pose?, string?)
		local existing = poses[part]
		if existing then
			return existing, nil
		end
		if depth > 64 then
			return nil, "hierarchy has a cycle near '" .. part .. "'"
		end
		local okName, nameErr = PathResolver.validName(part)
		if not okName then
			return nil, "pose part: " .. tostring(nameErr)
		end
		local parentInstance: Instance
		if isRootPart(part, hierarchy) then
			parentInstance = keyframe
		else
			local parentPart = hierarchy[part]
			if typeof(parentPart) ~= "string" then
				return nil, "part '" .. part .. "' is not in the hierarchy map"
			end
			local parentPose, parentErr = ensurePose(parentPart, depth + 1)
			if parentPose == nil then
				return nil, parentErr
			end
			parentInstance = parentPose
		end
		local pose = Instance.new("Pose")
		pose.Name = part -- R15 convention: Pose named after the part.
		pose.CFrame = CFrame.identity
		-- Structural (ancestor-only) poses carry no influence; posed parts get Weight = 1.
		pose.Weight = 0
		pose.EasingStyle = defaultStyle :: Enum.PoseEasingStyle
		pose.EasingDirection = defaultDirection :: Enum.PoseEasingDirection
		pose.Parent = parentInstance
		poses[part] = pose
		return pose, nil
	end

	-- Sort part names so construction is deterministic.
	local partNames: { string } = {}
	for part in pairs(frame.poses) do
		if typeof(part) ~= "string" then
			return abort(label .. ".poses keys must be part names")
		end
		table.insert(partNames, part)
	end
	table.sort(partNames)

	for _, part in ipairs(partNames) do
		local spec = frame.poses[part]
		if typeof(spec) ~= "table" then
			return abort(label .. ".poses." .. part .. " must be an object")
		end
		local pose, poseErr = ensurePose(part, 0)
		if pose == nil then
			return abort(label .. ": " .. tostring(poseErr))
		end
		if isRootPart(part, hierarchy) and part == ANIMATION_ROOT then
			-- Protocol: the root HumanoidRootPart pose is always identity.
			pose.CFrame = CFrame.identity
		else
			local cf, cfErr = poseCFrame(spec, label .. ".poses." .. part)
			if cf == nil then
				return abort(tostring(cfErr))
			end
			pose.CFrame = cf
		end
		pose.Weight = 1
		local style, sErr = easingStyle(spec.easing_style, defaultStyle :: Enum.PoseEasingStyle)
		if style == nil then
			return abort(label .. ".poses." .. part .. ": " .. tostring(sErr))
		end
		local direction, dErr = easingDirection(spec.easing_direction, defaultDirection :: Enum.PoseEasingDirection)
		if direction == nil then
			return abort(label .. ".poses." .. part .. ": " .. tostring(dErr))
		end
		pose.EasingStyle = style
		pose.EasingDirection = direction
	end

	return keyframe, nil
end

local function createAnimation(action: any): Result
	local okName, nameErr = PathResolver.validName(action.name)
	if not okName then
		return fail("name: " .. tostring(nameErr))
	end
	if action.rig ~= nil and action.rig ~= "R15" then
		return fail("only R15 rigs are supported (got " .. tostring(action.rig) .. ")")
	end
	if typeof(action.keyframes) ~= "table" or #action.keyframes == 0 then
		return fail("keyframes must be a non-empty array")
	end
	local hierarchy = action.hierarchy
	if hierarchy == nil then
		hierarchy = {}
	elseif typeof(hierarchy) ~= "table" then
		return fail("hierarchy must be an object")
	end

	local priority: Enum.AnimationPriority = Enum.AnimationPriority.Core
	if action.priority ~= nil then
		local found = false
		for _, item in ipairs(Enum.AnimationPriority:GetEnumItems()) do
			if item.Name == action.priority then
				priority = item :: any
				found = true
			end
		end
		if not found then
			return fail("unknown priority: " .. tostring(action.priority))
		end
	end

	local sequence = Instance.new("KeyframeSequence")
	sequence.Name = action.name
	sequence.Loop = action.looped == true
	sequence.Priority = priority

	local keyframes: { Keyframe } = {}
	for index, frame in ipairs(action.keyframes) do
		local keyframe, err = buildKeyframe(frame, index, hierarchy)
		if keyframe == nil then
			sequence:Destroy()
			return fail(tostring(err))
		end
		keyframe.Parent = sequence
		table.insert(keyframes, keyframe)
	end

	if action.markers ~= nil then
		if typeof(action.markers) ~= "table" then
			sequence:Destroy()
			return fail("markers must be an array")
		end
		for index, marker in ipairs(action.markers) do
			if typeof(marker) ~= "table" or typeof(marker.time) ~= "number" or typeof(marker.name) ~= "string" then
				sequence:Destroy()
				return fail("markers[" .. tostring(index) .. "] needs a numeric time and a string name")
			end
			-- Attach to the keyframe at or just before `time` (earliest keyframe as fallback).
			local target: Keyframe? = nil
			for _, keyframe in ipairs(keyframes) do
				if keyframe.Time <= marker.time and (target == nil or keyframe.Time >= (target :: Keyframe).Time) then
					target = keyframe
				end
			end
			if target == nil then
				target = keyframes[1]
				for _, keyframe in ipairs(keyframes) do
					if keyframe.Time < (target :: Keyframe).Time then
						target = keyframe
					end
				end
			end
			local keyframeMarker = Instance.new("KeyframeMarker")
			keyframeMarker.Name = marker.name
			keyframeMarker.Value = if typeof(marker.value) == "string" then marker.value else ""
			;(target :: Keyframe):AddMarker(keyframeMarker)
		end
	end

	local parentPath = if action.parent_path ~= nil then action.parent_path else DEFAULT_ANIMATION_PARENT
	local parent, parentErr = PathResolver.resolveOrCreateFolders(parentPath)
	if parent == nil then
		sequence:Destroy()
		return fail("parent_path: " .. tostring(parentErr))
	end
	sequence.Name = PathResolver.uniqueName(parent, action.name)
	sequence.Parent = parent

	-- Optional local preview id (not an uploaded asset). Failure is reported, not fatal.
	local previewId: string? = nil
	local previewOk, previewResult = pcall(function()
		return game:GetService("AnimationClipProvider"):RegisterKeyframeSequence(sequence)
	end)
	if previewOk and typeof(previewResult) == "string" then
		previewId = previewResult
	end

	local message = "created KeyframeSequence " .. (PathResolver.pathOf(sequence) or sequence.Name)
		.. " (" .. tostring(#keyframes) .. " keyframes, Loop=" .. tostring(sequence.Loop)
		.. ", Priority=" .. priority.Name .. ")"
	if previewId then
		message ..= "; preview id " .. previewId .. " (local to this Studio session; publish it to get a real asset id)"
	else
		message ..= "; could not register a preview id (" .. tostring(previewResult) .. ")"
	end
	return succeed(message, sequence, previewId)
end

local function deleteInstance(action: any): Result
	if typeof(action.path) ~= "string" then
		return fail("path must be a string")
	end
	if PathResolver.isServiceRoot(action.path) then
		return fail("refusing to delete a service: " .. action.path)
	end
	local target, err = PathResolver.resolve(action.path)
	if target == nil then
		return fail("path: " .. tostring(err))
	end
	local descendants = #target:GetDescendants()
	-- Parent = nil (rather than Destroy) keeps the instance restorable with Undo.
	target.Parent = nil
	return succeed("deleted " .. action.path .. " (" .. tostring(descendants) .. " descendant(s)); use Undo to restore it", nil)
end

local HANDLERS: { [string]: (any) -> Result } = {
	create_script = createScript,
	edit_script = editScript,
	create_instances = createInstances,
	create_animation = createAnimation,
	delete_instance = deleteInstance,
}

-- Apply one action as a single undo step. Never throws.
function ActionApplier.apply(action: any): Result
	if typeof(action) ~= "table" then
		return fail("action must be an object")
	end
	local handler = HANDLERS[action.type]
	if handler == nil then
		return fail("unsupported action type: " .. tostring(action.type))
	end

	local description = if typeof(action.description) == "string" then action.description else action.type
	local recording = ChangeHistoryService:TryBeginRecording("GODMODE: " .. action.type, "GODMODE: " .. description)
	if recording == nil then
		return fail("Studio refused to start an undo recording (another plugin is recording, or playtest is running); nothing was changed")
	end

	local ok, result = pcall(handler, action)
	if ok and result.ok then
		ChangeHistoryService:FinishRecording(recording, Enum.FinishRecordingOperation.Commit)
		if result.created and result.created.Parent ~= nil then
			pcall(function()
				Selection:Set({ result.created :: Instance })
			end)
		end
		return result
	end

	-- Cancel reverts everything done during the recording.
	ChangeHistoryService:FinishRecording(recording, Enum.FinishRecordingOperation.Cancel)
	if not ok then
		return fail("error while applying (changes reverted): " .. tostring(result))
	end
	return fail(result.message .. " (nothing was changed)")
end

return ActionApplier
