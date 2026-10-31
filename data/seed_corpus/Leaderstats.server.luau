--!strict
-- Creates the leaderboard display values for each player.
-- These IntValues are a read-only MIRROR: PlayerData.server owns the real
-- numbers (profile.coins) and writes them here. Never read leaderstats as the
-- source of truth or change it directly; go through ServerStorage.PlayerDataApi.

local Players = game:GetService("Players")

local function onPlayerAdded(player: Player)
	local leaderstats = Instance.new("Folder")
	leaderstats.Name = "leaderstats"

	local coins = Instance.new("IntValue")
	coins.Name = "Coins"
	coins.Value = 0 -- placeholder until PlayerData finishes loading the profile
	coins.Parent = leaderstats

	local kills = Instance.new("IntValue")
	kills.Name = "Kills"
	kills.Value = 0 -- session-only stat, intentionally not persisted
	kills.Parent = leaderstats

	leaderstats.Parent = player
end

Players.PlayerAdded:Connect(onPlayerAdded)
for _, player in Players:GetPlayers() do
	task.spawn(onPlayerAdded, player)
end
