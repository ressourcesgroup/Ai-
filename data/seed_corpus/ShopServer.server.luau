--!strict
-- Server-authoritative shop. The client only names an item; the server owns
-- prices and coins. Everything that can fail is resolved BEFORE charging, so a
-- missing template or backpack never costs the player coins.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerStorage = game:GetService("ServerStorage")

local RateLimiter = require(script.Parent.RateLimiter)

local PRICES: { [string]: number } = {
	Sword = 100,
	Shield = 75,
	SpeedPotion = 25,
}

-- PlayerData.server owns coin balances; leaderstats only mirrors them.
local playerDataApi = ServerStorage:WaitForChild("PlayerDataApi")
local spendCoins = playerDataApi:WaitForChild("SpendCoins") :: BindableFunction

local purchaseRemote = Instance.new("RemoteFunction")
purchaseRemote.Name = "PurchaseItem"
purchaseRemote.Parent = ReplicatedStorage

local limiter = RateLimiter.new(5, 1)

local function findTemplate(itemName: string): Tool?
	local items = ReplicatedStorage:FindFirstChild("Items")
	local template = items and items:FindFirstChild(itemName)
	if template and template:IsA("Tool") then
		return template
	end
	return nil
end

purchaseRemote.OnServerInvoke = function(player: Player, itemName: unknown)
	if not limiter:Allow(player) then
		return false, "Slow down"
	end
	if typeof(itemName) ~= "string" or #itemName > 32 then
		return false, "Invalid item"
	end
	local price = PRICES[itemName]
	if price == nil then
		return false, "Unknown item"
	end
	local template = findTemplate(itemName)
	local backpack = player:FindFirstChildOfClass("Backpack")
	if not template or not backpack then
		return false, "Item unavailable"
	end
	local item = template:Clone()
	-- SpendCoins checks and deducts the balance atomically on the server.
	if not spendCoins:Invoke(player, price) then
		item:Destroy()
		return false, "Not enough coins"
	end
	item.Parent = backpack
	return true, itemName
end
