--!strict
-- Intermission -> round -> results loop with a replicated status value.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local INTERMISSION = 15
local ROUND_LENGTH = 120
local MIN_PLAYERS = 2

local status = Instance.new("StringValue")
status.Name = "RoundStatus"
status.Parent = ReplicatedStorage

local function countdown(label: string, seconds: number, stopEarly: (() -> boolean)?)
	for remaining = seconds, 1, -1 do
		status.Value = `{label}: {remaining}`
		if stopEarly and stopEarly() then
			return
		end
		task.wait(1)
	end
end

local function alivePlayers(): { Player }
	local alive = {}
	for _, player in Players:GetPlayers() do
		local humanoid = player.Character and player.Character:FindFirstChildOfClass("Humanoid")
		if humanoid and humanoid.Health > 0 then
			table.insert(alive, player)
		end
	end
	return alive
end

while true do
	while #Players:GetPlayers() < MIN_PLAYERS do
		status.Value = `Waiting for {MIN_PLAYERS} players...`
		task.wait(2)
	end
	countdown("Intermission", INTERMISSION)
	for _, player in Players:GetPlayers() do
		player:LoadCharacter()
	end
	countdown("Round", ROUND_LENGTH, function()
		return #alivePlayers() <= 1
	end)
	local survivors = alivePlayers()
	if #survivors == 1 then
		status.Value = `{survivors[1].Name} wins!`
	else
		status.Value = "Round over"
	end
	task.wait(5)
end
