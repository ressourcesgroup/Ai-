--!strict
-- Server-authoritative shop. The client only names an item; the server owns prices.

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local RateLimiter = require(script.Parent.RateLimiter)

local PRICES: { [string]: number } = {
	Sword = 100,
	Shield = 75,
	SpeedPotion = 25,
}

local purchaseRemote = Instance.new("RemoteFunction")
purchaseRemote.Name = "PurchaseItem"
purchaseRemote.Parent = ReplicatedStorage

local limiter = RateLimiter.new(5, 1)

local function getCoins(player: Player): IntValue?
	local stats = player:FindFirstChild("leaderstats")
	local coins = stats and stats:FindFirstChild("Coins")
	if coins and coins:IsA("IntValue") then
		return coins
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
	local coins = getCoins(player)
	if not coins or coins.Value < price then
		return false, "Not enough coins"
	end
	coins.Value -= price
	local backpack = player:FindFirstChildOfClass("Backpack")
	local template = ReplicatedStorage:FindFirstChild("Items") and ReplicatedStorage.Items:FindFirstChild(itemName)
	if backpack and template then
		template:Clone().Parent = backpack
	end
	return true, itemName
end
