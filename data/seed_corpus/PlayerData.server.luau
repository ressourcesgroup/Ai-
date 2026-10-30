--!strict
-- Player data persistence: retries with backoff, autosave, and BindToClose.

local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local STORE = DataStoreService:GetDataStore("PlayerData_v1")
local MAX_ATTEMPTS = 5
local AUTOSAVE_INTERVAL = 120

type Profile = {
	version: number,
	coins: number,
	level: number,
	inventory: { string },
}

local DEFAULT_PROFILE: Profile = { version = 1, coins = 0, level = 1, inventory = {} }

local profiles: { [Player]: Profile } = {}

local function withRetries<T>(operation: () -> T): (boolean, T | string)
	local lastError = ""
	for attempt = 1, MAX_ATTEMPTS do
		local ok, result = pcall(operation)
		if ok then
			return true, result
		end
		lastError = tostring(result)
		task.wait(2 ^ attempt)
	end
	return false, lastError
end

local function migrate(data: any): Profile
	local profile = table.clone(DEFAULT_PROFILE)
	profile.inventory = {}
	if type(data) ~= "table" then
		return profile
	end
	if type(data.coins) == "number" then
		profile.coins = math.max(0, math.floor(data.coins))
	end
	if type(data.level) == "number" then
		profile.level = math.clamp(math.floor(data.level), 1, 1000)
	end
	if type(data.inventory) == "table" then
		for _, item in data.inventory do
			if type(item) == "string" then
				table.insert(profile.inventory, item)
			end
		end
	end
	return profile
end

local function load(player: Player)
	local key = "user_" .. player.UserId
	local ok, data = withRetries(function()
		return STORE:GetAsync(key)
	end)
	if not ok then
		player:Kick("Could not load your data. Please rejoin.")
		return
	end
	if player.Parent then
		profiles[player] = migrate(data)
	end
end

local function save(player: Player)
	local profile = profiles[player]
	if not profile then
		return
	end
	local key = "user_" .. player.UserId
	local ok, err = withRetries(function()
		return STORE:UpdateAsync(key, function()
			return profile
		end)
	end)
	if not ok then
		warn(`Failed to save {player.Name}: {err}`)
	end
end

Players.PlayerAdded:Connect(load)
Players.PlayerRemoving:Connect(function(player)
	save(player)
	profiles[player] = nil
end)

task.spawn(function()
	while true do
		task.wait(AUTOSAVE_INTERVAL)
		for player in profiles do
			task.spawn(save, player)
		end
	end
end)

game:BindToClose(function()
	local pending = 0
	for player in profiles do
		pending += 1
		task.spawn(function()
			save(player)
			pending -= 1
		end)
	end
	while pending > 0 do
		task.wait()
	end
end)
