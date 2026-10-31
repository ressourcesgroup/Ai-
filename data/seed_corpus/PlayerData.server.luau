--!strict
-- Player data persistence and the single source of truth for coins.
--
-- * Session locking: loads claim the key inside UpdateAsync with
--   { jobId, time }; another server's lock is respected until it expires.
-- * A failed load is never treated as a new player: the player is kicked and
--   nothing is written, so a DataStore outage cannot wipe saved data.
-- * Retries back off between attempts but never after the last one, and use
--   short delays during shutdown so BindToClose fits in its 30 second budget.
-- * Each player is saved-and-released exactly once on leave or shutdown.
-- * leaderstats.Coins (created by Leaderstats.server) is a display mirror;
--   other server scripts change coins only through the PlayerDataApi
--   BindableFunctions below.

local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")
local ServerStorage = game:GetService("ServerStorage")

local STORE = DataStoreService:GetDataStore("PlayerData_v1")
local MAX_ATTEMPTS = 4
local SHUTDOWN_ATTEMPTS = 2
local LOCK_EXPIRY = 30 * 60 -- seconds; a crashed server's lock is ignored after this
local LOCK_RETRIES = 3
local LOCK_RETRY_DELAY = 6
local AUTOSAVE_INTERVAL = 120
local SHUTDOWN_BUDGET = 25

type Profile = {
	version: number,
	coins: number,
	level: number,
	inventory: { string },
}

type Lock = { jobId: string, time: number }

local DEFAULT_PROFILE: Profile = { version = 1, coins = 0, level = 1, inventory = {} }

local profiles: { [Player]: Profile } = {}
local saving: { [Player]: boolean } = {}
local pendingReleases = 0
local shuttingDown = false

local function withRetries<T>(operation: () -> T): (boolean, T | string)
	local attempts = if shuttingDown then SHUTDOWN_ATTEMPTS else MAX_ATTEMPTS
	local lastError = ""
	for attempt = 1, attempts do
		local ok, result = pcall(operation)
		if ok then
			return true, result
		end
		lastError = tostring(result)
		if attempt < attempts then
			task.wait(if shuttingDown then 0.5 else 2 ^ attempt)
		end
	end
	return false, lastError
end

local function migrate(data: any): Profile
	local profile = table.clone(DEFAULT_PROFILE)
	profile.inventory = {}
	if type(data) ~= "table" then
		return profile
	end
	if type(data.coins) == "number" and data.coins == data.coins then
		profile.coins = math.max(0, math.floor(data.coins))
	end
	if type(data.level) == "number" and data.level == data.level then
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

local function isLockedByOtherServer(old: any): boolean
	if type(old) ~= "table" or type(old.lock) ~= "table" then
		return false
	end
	local lock = old.lock :: Lock
	if type(lock.time) ~= "number" then
		return false -- malformed lock; treat as expired
	end
	return lock.jobId ~= game.JobId and os.time() - lock.time < LOCK_EXPIRY
end

local function mirrorCoins(player: Player)
	local profile = profiles[player]
	local stats = player:FindFirstChild("leaderstats")
	local coins = stats and stats:FindFirstChild("Coins")
	if profile and coins and coins:IsA("IntValue") then
		coins.Value = profile.coins
	end
end

-- Writes `profile` under our lock. `release` clears the lock (leave/shutdown).
local function write(player: Player, profile: Profile, release: boolean): boolean
	local key = "user_" .. player.UserId
	local stolen = false
	local ok, err = withRetries(function()
		return STORE:UpdateAsync(key, function(old: any)
			stolen = isLockedByOtherServer(old)
			if stolen then
				return nil -- another server owns this session; do not overwrite it
			end
			local record = table.clone(profile) :: any
			record.lock = if release then nil else { jobId = game.JobId, time = os.time() }
			return record
		end)
	end)
	if not ok then
		warn(`Failed to save {player.Name}: {err}`)
	elseif stolen then
		warn(`Save for {player.Name} skipped: session is locked by another server`)
	end
	return ok and not stolen
end

-- Saves and forgets a player exactly once, whichever of leave/shutdown comes first.
local function release(player: Player)
	local profile = profiles[player]
	if not profile then
		return
	end
	profiles[player] = nil
	pendingReleases += 1
	while saving[player] do
		task.wait() -- let an in-flight autosave finish so it cannot re-lock after us
	end
	write(player, profile, true)
	pendingReleases -= 1
end

local function load(player: Player)
	local key = "user_" .. player.UserId
	for lockAttempt = 1, LOCK_RETRIES do
		local locked = false
		local ok, data = withRetries(function()
			return STORE:UpdateAsync(key, function(old: any)
				locked = isLockedByOtherServer(old)
				if locked then
					return nil
				end
				local record: any = if type(old) == "table" then table.clone(old) else {}
				record.lock = { jobId = game.JobId, time = os.time() }
				return record
			end)
		end)
		if not ok then
			-- Never fall back to a default profile here: saving it would erase real data.
			player:Kick("Could not load your data. Please rejoin.")
			return
		end
		if not locked then
			profiles[player] = migrate(data)
			if not player.Parent then
				release(player) -- left while loading; drop the lock we just took
				return
			end
			player:WaitForChild("leaderstats", 5) -- created by Leaderstats.server
			mirrorCoins(player)
			return
		end
		if lockAttempt < LOCK_RETRIES then
			task.wait(LOCK_RETRY_DELAY) -- the previous server is probably still saving
		end
		if not player.Parent then
			return
		end
	end
	player:Kick("Your data is still in use by another server. Please rejoin shortly.")
end

local MAX_COIN_DELTA = 1_000_000

local function validAmount(amount: unknown): boolean
	return type(amount) == "number" and amount == math.floor(amount) and amount > 0 and amount <= MAX_COIN_DELTA
end

-- Server-only API (ServerStorage is not replicated to clients).
local api = Instance.new("Folder")
api.Name = "PlayerDataApi"

local spendCoins = Instance.new("BindableFunction")
spendCoins.Name = "SpendCoins"
spendCoins.OnInvoke = function(player: Player, amount: number): boolean
	local profile = profiles[player]
	if not profile or not validAmount(amount) or profile.coins < amount then
		return false
	end
	profile.coins -= amount
	mirrorCoins(player)
	return true
end
spendCoins.Parent = api

local addCoins = Instance.new("BindableFunction")
addCoins.Name = "AddCoins"
addCoins.OnInvoke = function(player: Player, amount: number): boolean
	local profile = profiles[player]
	if not profile or not validAmount(amount) then
		return false
	end
	profile.coins += amount
	mirrorCoins(player)
	return true
end
addCoins.Parent = api

api.Parent = ServerStorage

Players.PlayerAdded:Connect(load)
for _, player in Players:GetPlayers() do
	task.spawn(load, player) -- players who joined before this script ran
end
Players.PlayerRemoving:Connect(release)

task.spawn(function()
	while not shuttingDown do
		task.wait(AUTOSAVE_INTERVAL)
		for player, profile in profiles do
			if not saving[player] and not shuttingDown then
				saving[player] = true
				task.spawn(function()
					write(player, profile, false)
					saving[player] = nil
				end)
			end
		end
	end
end)

game:BindToClose(function()
	shuttingDown = true
	for player in profiles do
		task.spawn(release, player)
	end
	local deadline = os.clock() + SHUTDOWN_BUDGET
	-- Also waits for releases started by PlayerRemoving before shutdown began.
	while pendingReleases > 0 and os.clock() < deadline do
		task.wait()
	end
end)
