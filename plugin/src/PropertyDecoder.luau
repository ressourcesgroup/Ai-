--!strict
-- PropertyDecoder: turns the JSON property encoding from docs/PLUGIN_PROTOCOL.md into
-- Roblox values. Only the value types below are accepted; anything else is refused.
-- Nothing here evaluates code: every value is built by a fixed constructor.
--
-- Supported encodings (type tags):
--   number / string / boolean   raw JSON value
--   Vector3     {"$type": "Vector3", "value": [x, y, z]}
--   Vector2     {"$type": "Vector2", "value": [x, y]}
--   CFrame      {"$type": "CFrame", "position": [x, y, z], "rotation": [rx, ry, rz]}  degrees,
--               CFrame.new(x, y, z) * CFrame.fromOrientation(rad rx, rad ry, rad rz)
--   Color3      {"$type": "Color3", "rgb": [r, g, b]}  0-255
--   UDim2       {"$type": "UDim2", "value": [xScale, xOffset, yScale, yOffset]}
--   UDim        {"$type": "UDim", "value": [scale, offset]}
--   Enum        {"$enum": "Material.Neon"}
--   BrickColor  {"$type": "BrickColor", "name": "Bright red"}
--   NumberRange {"$type": "NumberRange", "value": [min, max]}

local PropertyDecoder = {}

local MAX_STRING = 200000
local MAX_MAGNITUDE = 1e7

PropertyDecoder.SUPPORTED_TYPES = {
	"number",
	"string",
	"boolean",
	"Vector3",
	"Vector2",
	"CFrame",
	"Color3",
	"UDim2",
	"UDim",
	"Enum",
	"BrickColor",
	"NumberRange",
}

-- Properties the decoder never sets: Parent is assigned by the applier (last),
-- Name comes from the node's `name` field.
PropertyDecoder.BLOCKED_PROPERTIES = {
	Parent = true,
	Name = true,
} :: { [string]: boolean }

local function isFiniteNumber(value: any): boolean
	return typeof(value) == "number" and value == value and math.abs(value) <= MAX_MAGNITUDE
end

-- Read a fixed-length array of finite numbers.
local function numbers(value: any, count: number, label: string): ({ number }?, string?)
	if typeof(value) ~= "table" then
		return nil, label .. " must be an array of " .. tostring(count) .. " numbers"
	end
	if #value ~= count then
		return nil, label .. " must have exactly " .. tostring(count) .. " numbers"
	end
	local out: { number } = {}
	for i = 1, count do
		if not isFiniteNumber(value[i]) then
			return nil, label .. "[" .. tostring(i) .. "] is not a finite number"
		end
		out[i] = value[i]
	end
	return out, nil
end

local function decodeEnum(spec: any): (any, string?)
	if typeof(spec) ~= "string" then
		return nil, "$enum must be a string like 'Material.Neon'"
	end
	local enumName, itemName = (spec :: string):match("^([%a][%w]*)%.([%a][%w]*)$")
	if enumName == nil or itemName == nil then
		return nil, "malformed $enum: " .. spec
	end
	local ok, item = pcall(function()
		return (Enum :: any)[enumName][itemName]
	end)
	if not ok or typeof(item) ~= "EnumItem" then
		return nil, "unknown enum item: Enum." .. spec
	end
	return item, nil
end

-- Decode one encoded value. Returns (value, nil) or (nil, error message).
function PropertyDecoder.decode(encoded: any): (any, string?)
	local kind = typeof(encoded)
	if kind == "number" then
		if encoded ~= encoded then
			return nil, "NaN is not allowed"
		end
		return encoded, nil
	elseif kind == "boolean" then
		return encoded, nil
	elseif kind == "string" then
		if #encoded > MAX_STRING then
			return nil, "string value too long"
		end
		return encoded, nil
	elseif kind ~= "table" then
		return nil, "unsupported value of type " .. kind
	end

	if encoded["$enum"] ~= nil then
		return decodeEnum(encoded["$enum"])
	end

	local tag = encoded["$type"]
	if tag == "Vector3" then
		local v, err = numbers(encoded.value, 3, "Vector3.value")
		if v == nil then
			return nil, err
		end
		return Vector3.new(v[1], v[2], v[3]), nil
	elseif tag == "Vector2" then
		local v, err = numbers(encoded.value, 2, "Vector2.value")
		if v == nil then
			return nil, err
		end
		return Vector2.new(v[1], v[2]), nil
	elseif tag == "CFrame" then
		local p, perr = numbers(encoded.position, 3, "CFrame.position")
		if p == nil then
			return nil, perr
		end
		local r: { number } = { 0, 0, 0 }
		if encoded.rotation ~= nil then
			local rotation, rerr = numbers(encoded.rotation, 3, "CFrame.rotation")
			if rotation == nil then
				return nil, rerr
			end
			r = rotation
		end
		-- Same convention as the Orientation property (Y, then X, then Z), degrees.
		return CFrame.new(p[1], p[2], p[3]) * CFrame.fromOrientation(math.rad(r[1]), math.rad(r[2]), math.rad(r[3])), nil
	elseif tag == "Color3" then
		local c, err = numbers(encoded.rgb, 3, "Color3.rgb")
		if c == nil then
			return nil, err
		end
		for i = 1, 3 do
			if c[i] < 0 or c[i] > 255 then
				return nil, "Color3.rgb values must be 0-255"
			end
		end
		return Color3.fromRGB(c[1], c[2], c[3]), nil
	elseif tag == "UDim2" then
		local u, err = numbers(encoded.value, 4, "UDim2.value")
		if u == nil then
			return nil, err
		end
		return UDim2.new(u[1], u[2], u[3], u[4]), nil
	elseif tag == "UDim" then
		local u, err = numbers(encoded.value, 2, "UDim.value")
		if u == nil then
			return nil, err
		end
		return UDim.new(u[1], u[2]), nil
	elseif tag == "BrickColor" then
		if typeof(encoded.name) ~= "string" then
			return nil, "BrickColor.name must be a string"
		end
		local ok, color = pcall(function()
			return BrickColor.new(encoded.name)
		end)
		if not ok then
			return nil, "invalid BrickColor: " .. tostring(encoded.name)
		end
		return color, nil
	elseif tag == "NumberRange" then
		local n, err = numbers(encoded.value, 2, "NumberRange.value")
		if n == nil then
			return nil, err
		end
		if n[1] > n[2] then
			return nil, "NumberRange min must be <= max"
		end
		return NumberRange.new(n[1], n[2]), nil
	end

	return nil, "unsupported value type: " .. tostring(tag)
end

-- Decode and assign one property. Returns nil on success or an error message.
function PropertyDecoder.apply(instance: Instance, property: string, encoded: any): string?
	if typeof(property) ~= "string" or property == "" then
		return "property name must be a non-empty string"
	end
	if PropertyDecoder.BLOCKED_PROPERTIES[property] then
		return "property '" .. property .. "' cannot be set by the server"
	end
	local value, err = PropertyDecoder.decode(encoded)
	if err ~= nil then
		return property .. ": " .. err
	end
	local ok, setErr = pcall(function()
		(instance :: any)[property] = value
	end)
	if not ok then
		return property .. ": " .. tostring(setErr)
	end
	return nil
end

-- Attribute values are limited to number / string / boolean (protocol).
function PropertyDecoder.applyAttribute(instance: Instance, name: any, value: any): string?
	if typeof(name) ~= "string" or name == "" then
		return "attribute name must be a non-empty string"
	end
	local kind = typeof(value)
	if kind ~= "number" and kind ~= "string" and kind ~= "boolean" then
		return "attribute " .. name .. " must be a number, string or boolean"
	end
	local ok, err = pcall(function()
		instance:SetAttribute(name, value)
	end)
	if not ok then
		return "attribute " .. name .. ": " .. tostring(err)
	end
	return nil
end

return PropertyDecoder
