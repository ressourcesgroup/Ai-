--!strict
-- Intermission -> round -> results loop with a replicated status value.
-- Characters do not auto-load: the server spawns everyone explicitly when a
-- round starts, so a player who dies stays out until the next round instead
-- of respawning and being counted as alive again.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local INTERMISSION = 15
local ROUND_LENGTH = 120
local MIN_PLAYERS = 2

Players.CharacterAutoLoads = false

local status = Instance.new("StringValue")
status.Name = "RoundStatus"
status.Parent = ReplicatedStorage

-- Players still in the current round. Cleared on death or leaving.
local participants: { [Player]: true } = {}
local deathConnections: { [Player]: RBXScriptConnection } = {}

local function eliminate(player: Player)
	participants[player] = nil
	local connection = deathConnections[player]
	if connection then
		connection:Disconnect()
		deathConnections[player] = nil
	end
end

local function participantCount(): number
	local count = 0
	for _ in participants do
		count += 1
	end
	return count
end

local function countdown(label: string, seconds: number, stopEarly: (() -> boolean)?)
	for remaining = seconds, 1, -1 do
		status.Value = `{label}: {remaining}`
		if stopEarly and stopEarly() then
			return
		end
		task.wait(1)
	end
end

local function startRound()
	for _, player in Players:GetPlayers() do
		-- LoadCharacter yields; a player can leave while we wait.
		local ok = pcall(function()
			player:LoadCharacter()
		end)
		local character = player.Character
		local humanoid = character and character:FindFirstChildOfClass("Humanoid")
		if ok and player.Parent and humanoid then
			participants[player] = true
			deathConnections[player] = humanoid.Died:Connect(function()
				eliminate(player)
			end)
		end
	end
end

local function endRound()
	for player in participants do
		eliminate(player)
	end
	-- Clear arenas: everyone waits in the lobby (no character) until next round.
	for _, player in Players:GetPlayers() do
		local character = player.Character
		if character then
			player.Character = nil
			character:Destroy()
		end
	end
end

-- New joiners have no character (spectate) until the next round starts.
Players.PlayerRemoving:Connect(eliminate)

while true do
	while #Players:GetPlayers() < MIN_PLAYERS do
		status.Value = `Waiting for {MIN_PLAYERS} players...`
		task.wait(2)
	end
	countdown("Intermission", INTERMISSION)
	startRound()
	countdown("Round", ROUND_LENGTH, function()
		return participantCount() <= 1
	end)
	local winner = next(participants)
	if winner and participantCount() == 1 then
		status.Value = `{winner.Name} wins!`
	else
		status.Value = "Round over"
	end
	endRound()
	task.wait(5)
end
