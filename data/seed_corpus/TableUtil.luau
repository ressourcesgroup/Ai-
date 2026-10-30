--!strict
-- Small functional helpers for arrays and dictionaries.

local TableUtil = {}

function TableUtil.map<T, U>(list: { T }, fn: (T, number) -> U): { U }
	local out = table.create(#list)
	for i, v in list do
		out[i] = fn(v, i)
	end
	return out
end

function TableUtil.filter<T>(list: { T }, predicate: (T) -> boolean): { T }
	local out = {}
	for _, v in list do
		if predicate(v) then
			table.insert(out, v)
		end
	end
	return out
end

function TableUtil.reduce<T, A>(list: { T }, fn: (A, T) -> A, initial: A): A
	local acc = initial
	for _, v in list do
		acc = fn(acc, v)
	end
	return acc
end

function TableUtil.keys<K, V>(dict: { [K]: V }): { K }
	local out = {}
	for k in dict do
		table.insert(out, k)
	end
	return out
end

function TableUtil.deepCopy<T>(value: T): T
	if type(value) ~= "table" then
		return value
	end
	local copy = {}
	for k, v in value :: any do
		copy[TableUtil.deepCopy(k)] = TableUtil.deepCopy(v)
	end
	return setmetatable(copy, getmetatable(value :: any)) :: any
end

function TableUtil.shuffle<T>(list: { T }, rng: Random?): { T }
	local random = rng or Random.new()
	local out = table.clone(list)
	for i = #out, 2, -1 do
		local j = random:NextInteger(1, i)
		out[i], out[j] = out[j], out[i]
	end
	return out
end

return TableUtil
