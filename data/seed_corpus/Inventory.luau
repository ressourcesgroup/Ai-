--!strict
-- Pure inventory logic with stacking. No Roblox APIs, so it is unit-testable.

export type Stack = { id: string, count: number }
export type Inventory = { slots: { Stack? }, capacity: number, maxStack: number }

local Inventory = {}

function Inventory.new(capacity: number, maxStack: number): Inventory
	return { slots = table.create(capacity), capacity = capacity, maxStack = maxStack }
end

function Inventory.count(inv: Inventory, id: string): number
	local total = 0
	for i = 1, inv.capacity do
		local stack = inv.slots[i]
		if stack and stack.id == id then
			total += stack.count
		end
	end
	return total
end

-- Amounts must be positive whole numbers. Rejects 0, negatives, fractions,
-- NaN and infinity, which would otherwise corrupt stack counts.
local function isValidAmount(amount: number): boolean
	return type(amount) == "number" and amount > 0 and amount < math.huge and amount == math.floor(amount)
end

-- Returns how many items could not be added. Errors on an invalid amount.
function Inventory.add(inv: Inventory, id: string, amount: number): number
	assert(isValidAmount(amount), "amount must be a positive integer")
	local remaining = amount
	for i = 1, inv.capacity do
		local stack = inv.slots[i]
		if stack and stack.id == id and stack.count < inv.maxStack then
			local moved = math.min(inv.maxStack - stack.count, remaining)
			stack.count += moved
			remaining -= moved
		end
		if remaining == 0 then
			return 0
		end
	end
	for i = 1, inv.capacity do
		if inv.slots[i] == nil then
			local moved = math.min(inv.maxStack, remaining)
			inv.slots[i] = { id = id, count = moved }
			remaining -= moved
		end
		if remaining == 0 then
			return 0
		end
	end
	return remaining
end

-- Returns false (and changes nothing) for an invalid amount or too few items.
function Inventory.remove(inv: Inventory, id: string, amount: number): boolean
	if not isValidAmount(amount) or Inventory.count(inv, id) < amount then
		return false
	end
	local remaining = amount
	for i = inv.capacity, 1, -1 do
		local stack = inv.slots[i]
		if stack and stack.id == id then
			local taken = math.min(stack.count, remaining)
			stack.count -= taken
			remaining -= taken
			if stack.count == 0 then
				inv.slots[i] = nil
			end
		end
		if remaining == 0 then
			break
		end
	end
	return true
end

return Inventory
