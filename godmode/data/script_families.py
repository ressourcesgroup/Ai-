"""Supervised fine-tuning data: natural-language request -> ONE COMPLETE, runnable Luau script.

Every family below is a parameterised generator that writes a whole, self-contained
script (it finds or creates the parts it needs, uses services correctly, validates
remotes on the server, wraps DataStore calls in pcall, uses task.* instead of the
deprecated globals). Each sample is gated by the repo's verifier (check_structure +
lint) before it is kept, exactly like ``synthetic.py``.

The first line of every solution is a placement header the server parses:

    -- Script in ServerScriptService: <title>
    -- LocalScript in StarterPlayer.StarterPlayerScripts: <title>
    -- LocalScript in StarterPlayer.StarterCharacterScripts: <title>
    -- ModuleScript in ReplicatedStorage: <title>

Honesty note: these are templates written by a person (with randomised names,
numbers, options and paraphrased prompts) used as *training targets*. Their purpose
is to teach the small model to emit whole scripts instead of fragments; how well the
model generalises beyond these families is measured separately (held-out families).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable

from ..luau import check_structure, lint
from .corpus import Document

COLORS = {"red": (255, 0, 0), "green": (0, 200, 70), "blue": (0, 120, 255), "yellow": (255, 210, 0),
          "purple": (150, 60, 220), "orange": (255, 140, 0), "white": (240, 240, 240), "black": (20, 20, 20),
          "pink": (255, 105, 180), "cyan": (0, 220, 230)}
KEYS = ["LeftShift", "Q", "E", "F", "R", "C", "X", "Z", "V", "G"]
STATS = ["Coins", "Cash", "Gems", "Points", "Gold", "Money", "Score", "Diamonds", "Tokens", "Wins"]

ASK = ["", "", "", "make ", "make me ", "create ", "write ", "write a script that makes ", "can you make ", "i need ",
       "pls make ", "how do i make ", "code ", "script ", "give me ", "roblox: ", "build me ", "help me make ", "add "]
TAIL = ["", "", "", "", " in roblox", " for my game", " please", " pls", " (full script)", " script", " for roblox studio",
        " that works", " with comments"]


@dataclass
class ScriptSample:
    family: str
    prompt: str
    solution: str

    def to_document(self) -> Document:
        text = f"<|user|>{self.prompt}\n<|assistant|>{self.solution}"
        return Document(text, "godmode-scripts-v1", "MIT", f"synthetic://scripts/{self.family}", kind="chat",
                        meta={"task": f"script:{self.family}"})


def _phrase(rng: random.Random, core: str) -> str:
    p = rng.choice(ASK) + core + rng.choice(TAIL)
    p = re.sub(r"\s+", " ", p).strip()
    if rng.random() < 0.3:
        p = p[0].upper() + p[1:]
    if rng.random() < 0.15:
        p += rng.choice(["?", ".", "!"])
    return p


def _rgb(rng: random.Random) -> tuple[str, tuple[int, int, int]]:
    name = rng.choice(list(COLORS))
    return name, COLORS[name]


def _c3(rgb: tuple[int, int, int]) -> str:
    return f"Color3.fromRGB({rgb[0]}, {rgb[1]}, {rgb[2]})"


def _part_block(var: str, name: str, size: tuple, pos: tuple, rgb: tuple, material: str = "SmoothPlastic",
                extra: str = "") -> str:
    return (f'local {var} = workspace:FindFirstChild("{name}")\n'
            f"if not {var} then\n"
            f'\t{var} = Instance.new("Part")\n'
            f'\t{var}.Name = "{name}"\n'
            f"\t{var}.Size = Vector3.new({size[0]}, {size[1]}, {size[2]})\n"
            f"\t{var}.Position = Vector3.new({pos[0]}, {pos[1]}, {pos[2]})\n"
            f"\t{var}.Anchored = true\n"
            f"\t{var}.Color = {_c3(rgb)}\n"
            f"\t{var}.Material = Enum.Material.{material}\n"
            f"{extra}"
            f"\t{var}.Parent = workspace\n"
            "end\n")


SERVER = "-- Script in ServerScriptService: "
LOCAL_PLAYER = "-- LocalScript in StarterPlayer.StarterPlayerScripts: "
LOCAL_CHAR = "-- LocalScript in StarterPlayer.StarterCharacterScripts: "
MODULE = "-- ModuleScript in ReplicatedStorage: "


# ----------------------------------------------------------------------------- families

def kill_brick(rng):
    name = rng.choice(["KillBrick", "Lava", "DeathPart", "Spikes", "AcidPool", "LavaFloor"])
    cname, rgb = rng.choice([("red", COLORS["red"]), ("orange", COLORS["orange"]), ("green", COLORS["green"])])
    damage = rng.choice([None, None, 10, 20, 25, 50])
    core = rng.choice([f"a kill brick", f"a {cname} kill part", "lava that kills you", f"a part called {name} that kills players",
                       "a death brick", "a part that kills the player when touched"])
    if damage:
        core = rng.choice([f"a part that does {damage} damage when touched", f"a damage brick that deals {damage} damage",
                           f"lava that hurts players for {damage} damage"])
    mat = "Neon" if rng.random() < 0.5 else "SmoothPlastic"
    lines = [SERVER + ("damage brick" if damage else "kill brick"),
             _part_block("brick", name, (rng.choice([8, 10, 12, 16]), 1, rng.choice([8, 10, 12, 16])), (0, 0.5, rng.choice([0, 10, 20])), rgb, mat)]
    if damage:
        cd = rng.choice([0.5, 1, 1.5])
        lines.append(f"local DAMAGE = {damage}\nlocal COOLDOWN = {cd}\nlocal lastHit = {{}}\n\n"
                     "brick.Touched:Connect(function(hit)\n"
                     "\tlocal character = hit.Parent\n"
                     '\tlocal humanoid = character and character:FindFirstChildOfClass("Humanoid")\n'
                     "\tif not humanoid or humanoid.Health <= 0 then\n\t\treturn\n\tend\n"
                     "\tlocal now = os.clock()\n"
                     "\tif lastHit[humanoid] and now - lastHit[humanoid] < COOLDOWN then\n\t\treturn\n\tend\n"
                     "\tlastHit[humanoid] = now\n"
                     "\thumanoid:TakeDamage(DAMAGE)\n"
                     "end)\n")
    else:
        lines.append("brick.Touched:Connect(function(hit)\n"
                     "\tlocal character = hit.Parent\n"
                     '\tlocal humanoid = character and character:FindFirstChildOfClass("Humanoid")\n'
                     "\tif humanoid and humanoid.Health > 0 then\n"
                     "\t\thumanoid.Health = 0\n"
                     "\tend\n"
                     "end)\n")
    return core, "\n".join(lines)


def coin_pickup(rng):
    stat = rng.choice(STATS)
    value = rng.choice([1, 5, 10, 25, 50, 100])
    respawn = rng.choice([3, 5, 10, 15])
    count = rng.choice([1, 5, 8, 10, 12, 20])
    core = rng.choice([f"coins that give {value} {stat.lower()} when you touch them", f"{count} collectible coins",
                       f"a coin pickup that gives {stat}", f"collectable coins worth {value} that respawn after {respawn} seconds",
                       f"spawn {count} coins players can collect"])
    src = f"""{SERVER}collectible coins
local Players = game:GetService("Players")

local STAT_NAME = "{stat}"
local COIN_VALUE = {value}
local RESPAWN_TIME = {respawn}
local COIN_COUNT = {count}

local function getStat(player)
	local leaderstats = player:FindFirstChild("leaderstats")
	if not leaderstats then
		leaderstats = Instance.new("Folder")
		leaderstats.Name = "leaderstats"
		leaderstats.Parent = player
	end
	local stat = leaderstats:FindFirstChild(STAT_NAME)
	if not stat then
		stat = Instance.new("IntValue")
		stat.Name = STAT_NAME
		stat.Parent = leaderstats
	end
	return stat
end

Players.PlayerAdded:Connect(getStat)

local folder = Instance.new("Folder")
folder.Name = "Coins"
folder.Parent = workspace

local function makeCoin(index)
	local coin = Instance.new("Part")
	coin.Name = "Coin" .. index
	coin.Shape = Enum.PartType.Cylinder
	coin.Size = Vector3.new(0.4, 3, 3)
	coin.Color = Color3.fromRGB(255, 200, 0)
	coin.Material = Enum.Material.Neon
	coin.Anchored = true
	coin.CanCollide = false
	coin.CFrame = CFrame.new(index * 6, 3, 0) * CFrame.Angles(0, 0, math.rad(90))
	coin.Parent = folder

	local collected = false
	coin.Touched:Connect(function(hit)
		if collected then
			return
		end
		local player = Players:GetPlayerFromCharacter(hit.Parent)
		if not player then
			return
		end
		collected = true
		getStat(player).Value += COIN_VALUE
		coin.Transparency = 1
		task.wait(RESPAWN_TIME)
		coin.Transparency = 0
		collected = false
	end)
end

for i = 1, COIN_COUNT do
	makeCoin(i)
end
"""
    return core, src


def teleporter(rng):
    a, b = rng.choice([("PadA", "PadB"), ("TeleportIn", "TeleportOut"), ("Portal1", "Portal2"), ("StartPad", "EndPad")])
    dist = rng.choice([30, 50, 80, 100, 150])
    cd = rng.choice([1, 2, 3])
    cname, rgb = _rgb(rng)
    two_way = rng.random() < 0.6
    core = rng.choice(["a teleporter", "two teleport pads", "a teleport pad that sends you to another pad",
                       f"a {cname} teleporter", "a portal that teleports the player", "teleport parts"])
    src = SERVER + "teleport pads\n"
    src += _part_block("padA", a, (6, 1, 6), (0, 0.5, 0), rgb, "Neon")
    src += _part_block("padB", b, (6, 1, 6), (dist, 0.5, 0), rgb, "Neon")
    src += f"""
local COOLDOWN = {cd}
local lastTeleport = {{}}

local function connect(fromPad, toPad)
	fromPad.Touched:Connect(function(hit)
		local character = hit.Parent
		local humanoid = character and character:FindFirstChildOfClass("Humanoid")
		local root = character and character:FindFirstChild("HumanoidRootPart")
		if not humanoid or not root then
			return
		end
		local now = os.clock()
		if lastTeleport[character] and now - lastTeleport[character] < COOLDOWN then
			return
		end
		lastTeleport[character] = now
		root.CFrame = toPad.CFrame + Vector3.new(0, 4, 0)
	end)
end

connect(padA, padB)
"""
    if two_way:
        src += "connect(padB, padA)\n"
    return core, src


def sprint(rng):
    key = rng.choice(KEYS[:3])
    walk = rng.choice([16])
    run = rng.choice([24, 28, 30, 32, 35, 40])
    stamina = rng.random() < 0.5
    core = rng.choice([f"a sprint script with {key}", "sprinting when holding shift", f"run when you hold {key}",
                       f"a shift to sprint system with speed {run}", "a sprint with stamina" if stamina else "a sprint button",
                       "make the player run faster when holding shift"])
    src = f"""{LOCAL_CHAR}sprint
local UserInputService = game:GetService("UserInputService")
local RunService = game:GetService("RunService")

local character = script.Parent
local humanoid = character:WaitForChild("Humanoid")

local WALK_SPEED = {walk}
local SPRINT_SPEED = {run}
local SPRINT_KEY = Enum.KeyCode.{key}
"""
    if stamina:
        src += f"""local MAX_STAMINA = {rng.choice([50, 100, 150])}
local DRAIN_PER_SECOND = {rng.choice([15, 20, 25])}
local REGEN_PER_SECOND = {rng.choice([8, 10, 12])}

local stamina = MAX_STAMINA
local wantsSprint = false

UserInputService.InputBegan:Connect(function(input, processed)
	if not processed and input.KeyCode == SPRINT_KEY then
		wantsSprint = true
	end
end)

UserInputService.InputEnded:Connect(function(input)
	if input.KeyCode == SPRINT_KEY then
		wantsSprint = false
	end
end)

RunService.Heartbeat:Connect(function(dt)
	local moving = humanoid.MoveDirection.Magnitude > 0
	if wantsSprint and moving and stamina > 0 then
		stamina = math.max(0, stamina - DRAIN_PER_SECOND * dt)
		humanoid.WalkSpeed = SPRINT_SPEED
	else
		stamina = math.min(MAX_STAMINA, stamina + REGEN_PER_SECOND * dt)
		humanoid.WalkSpeed = WALK_SPEED
	end
end)
"""
    else:
        src += """
UserInputService.InputBegan:Connect(function(input, processed)
	if not processed and input.KeyCode == SPRINT_KEY then
		humanoid.WalkSpeed = SPRINT_SPEED
	end
end)

UserInputService.InputEnded:Connect(function(input)
	if input.KeyCode == SPRINT_KEY then
		humanoid.WalkSpeed = WALK_SPEED
	end
end)
"""
    return core, src


def double_jump(rng):
    jumps = rng.choice([2, 2, 3])
    power = rng.choice([40, 50, 60])
    core = rng.choice(["double jump", "a double jump script", f"let players jump {jumps} times in the air" if jumps > 2 else "jump twice",
                       "a multi jump", "air jump"])
    src = f"""{LOCAL_CHAR}double jump
local UserInputService = game:GetService("UserInputService")

local character = script.Parent
local humanoid = character:WaitForChild("Humanoid")
local root = character:WaitForChild("HumanoidRootPart")

local MAX_JUMPS = {jumps}
local JUMP_POWER = {power}
local jumpsUsed = 0

humanoid.StateChanged:Connect(function(_, newState)
	if newState == Enum.HumanoidStateType.Landed then
		jumpsUsed = 0
	elseif newState == Enum.HumanoidStateType.Jumping then
		jumpsUsed = math.max(jumpsUsed, 1)
	end
end)

UserInputService.JumpRequest:Connect(function()
	local state = humanoid:GetState()
	if state == Enum.HumanoidStateType.Freefall and jumpsUsed < MAX_JUMPS then
		jumpsUsed += 1
		local velocity = root.AssemblyLinearVelocity
		root.AssemblyLinearVelocity = Vector3.new(velocity.X, JUMP_POWER, velocity.Z)
		humanoid:ChangeState(Enum.HumanoidStateType.Jumping)
	end
end)
"""
    return core, src


def day_night(rng):
    minutes = rng.choice([2, 5, 8, 10, 12, 20])
    start = rng.choice([6, 8, 12, 14])
    core = rng.choice(["a day night cycle", f"day and night that lasts {minutes} minutes", "a day/night cycle script",
                       "make time pass in the game", f"a {minutes} minute day cycle"])
    smooth = rng.random() < 0.5
    src = f"""{SERVER}day/night cycle
local Lighting = game:GetService("Lighting")

local DAY_LENGTH_MINUTES = {minutes}
local HOURS_PER_SECOND = 24 / (DAY_LENGTH_MINUTES * 60)

Lighting.ClockTime = {start}
"""
    if smooth:
        src += """local RunService = game:GetService("RunService")

RunService.Heartbeat:Connect(function(dt)
	Lighting.ClockTime = (Lighting.ClockTime + HOURS_PER_SECOND * dt) % 24
end)
"""
    else:
        src += """
while true do
	local dt = task.wait(1)
	Lighting.ClockTime = (Lighting.ClockTime + HOURS_PER_SECOND * dt) % 24
end
"""
    return core, src


def round_system(rng):
    inter = rng.choice([10, 15, 20, 30])
    rtime = rng.choice([60, 90, 120, 180])
    minp = rng.choice([1, 2])
    core = rng.choice(["a round system", f"rounds with {inter} second intermission", "an intermission and round loop",
                       f"a game loop with {rtime} second rounds", "a minigame round system that teleports players"])
    src = f"""{SERVER}round system
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local INTERMISSION = {inter}
local ROUND_TIME = {rtime}
local MIN_PLAYERS = {minp}

local status = ReplicatedStorage:FindFirstChild("Status")
if not status then
	status = Instance.new("StringValue")
	status.Name = "Status"
	status.Parent = ReplicatedStorage
end

local lobby = workspace:FindFirstChild("Lobby")
if not lobby then
	lobby = Instance.new("SpawnLocation")
	lobby.Name = "Lobby"
	lobby.Size = Vector3.new(12, 1, 12)
	lobby.Position = Vector3.new(0, 0.5, 0)
	lobby.Anchored = true
	lobby.Parent = workspace
end

local arena = workspace:FindFirstChild("ArenaSpawn")
if not arena then
	arena = Instance.new("Part")
	arena.Name = "ArenaSpawn"
	arena.Size = Vector3.new(40, 1, 40)
	arena.Position = Vector3.new(0, 0.5, 120)
	arena.Anchored = true
	arena.Parent = workspace
end

local function teleportAll(target)
	for _, player in Players:GetPlayers() do
		local character = player.Character
		local root = character and character:FindFirstChild("HumanoidRootPart")
		if root then
			local offset = Vector3.new(math.random(-8, 8), 4, math.random(-8, 8))
			root.CFrame = CFrame.new(target.Position + offset)
		end
	end
end

local function alivePlayers()
	local alive = {{}}
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
		status.Value = "Waiting for players..."
		task.wait(1)
	end

	for i = INTERMISSION, 1, -1 do
		status.Value = "Intermission: " .. i
		task.wait(1)
	end

	teleportAll(arena)
	for i = ROUND_TIME, 1, -1 do
		status.Value = "Round: " .. i
		if #alivePlayers() == 0 then
			break
		end
		task.wait(1)
	end

	status.Value = "Round over!"
	teleportAll(lobby)
	task.wait(3)
end
"""
    return core, src


def sword_tool(rng):
    name = rng.choice(["Sword", "Blade", "Katana", "ClassicSword", "Saber"])
    dmg = rng.choice([10, 15, 20, 25, 30, 35])
    cd = rng.choice([0.4, 0.5, 0.6, 0.8, 1])
    core = rng.choice([f"a sword that does {dmg} damage", "a sword tool", f"a {name.lower()} players get when they spawn",
                       "a melee weapon", f"a sword with {cd} second cooldown", "a working sword"])
    src = f"""{SERVER}{name.lower()} tool
local Players = game:GetService("Players")

local DAMAGE = {dmg}
local COOLDOWN = {cd}
local TOOL_NAME = "{name}"

local function makeSword()
	local tool = Instance.new("Tool")
	tool.Name = TOOL_NAME
	tool.RequiresHandle = true
	tool.CanBeDropped = false

	local handle = Instance.new("Part")
	handle.Name = "Handle"
	handle.Size = Vector3.new(0.5, 0.5, 4)
	handle.Color = Color3.fromRGB(180, 180, 190)
	handle.Material = Enum.Material.Metal
	handle.CanCollide = false
	handle.Parent = tool
	tool.Grip = CFrame.new(0, 0, 1.5)

	local swinging = false
	local lastSwing = 0
	local hitThisSwing = {{}}

	tool.Activated:Connect(function()
		local now = os.clock()
		if now - lastSwing < COOLDOWN then
			return
		end
		lastSwing = now
		swinging = true
		hitThisSwing = {{}}
		task.delay(0.35, function()
			swinging = false
		end)
	end)

	handle.Touched:Connect(function(hit)
		if not swinging then
			return
		end
		local owner = tool.Parent
		local victim = hit.Parent
		if victim == owner then
			return
		end
		local humanoid = victim and victim:FindFirstChildOfClass("Humanoid")
		if humanoid and humanoid.Health > 0 and not hitThisSwing[humanoid] then
			hitThisSwing[humanoid] = true
			humanoid:TakeDamage(DAMAGE)
		end
	end)

	return tool
end

Players.PlayerAdded:Connect(function(player)
	player.CharacterAdded:Connect(function()
		local backpack = player:WaitForChild("Backpack")
		if not backpack:FindFirstChild(TOOL_NAME) then
			makeSword().Parent = backpack
		end
	end)
end)
"""
    return core, src


def shop(rng):
    stat = rng.choice(STATS[:6])
    items = rng.sample([("SpeedCoil", 100), ("GravityCoil", 150), ("Sword", 50), ("Medkit", 25), ("Flashlight", 10),
                        ("Rocket", 300), ("Shield", 200)], k=rng.choice([2, 3, 4]))
    core = rng.choice(["a shop system", f"a shop where you buy items with {stat.lower()}", "a server shop with a remote",
                       "a secure item shop", f"let players buy {items[0][0]} for {items[0][1]} {stat.lower()}"])
    item_lines = "\n".join(f"\t{n} = {p}," for n, p in items)
    src = f"""{SERVER}shop (server-validated purchases)
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local CURRENCY = "{stat}"
local PRICES = {{
{item_lines}
}}

local remote = ReplicatedStorage:FindFirstChild("BuyItem")
if not remote then
	remote = Instance.new("RemoteFunction")
	remote.Name = "BuyItem"
	remote.Parent = ReplicatedStorage
end

local lastRequest = {{}}

remote.OnServerInvoke = function(player, itemName)
	if typeof(itemName) ~= "string" then
		return false, "bad request"
	end
	local price = PRICES[itemName]
	if not price then
		return false, "unknown item"
	end
	local now = os.clock()
	if lastRequest[player] and now - lastRequest[player] < 0.5 then
		return false, "slow down"
	end
	lastRequest[player] = now

	local leaderstats = player:FindFirstChild("leaderstats")
	local money = leaderstats and leaderstats:FindFirstChild(CURRENCY)
	if not money or money.Value < price then
		return false, "not enough " .. CURRENCY
	end
	money.Value -= price

	local tool = Instance.new("Tool")
	tool.Name = itemName
	tool.RequiresHandle = false
	tool.Parent = player:WaitForChild("Backpack")
	return true, "bought " .. itemName
end

Players.PlayerRemoving:Connect(function(player)
	lastRequest[player] = nil
end)
"""
    return core, src


def leaderstats_simple(rng):
    stats = rng.sample(STATS, k=rng.choice([1, 2, 3]))
    start = rng.choice([0, 0, 10, 100])
    core = rng.choice([f"leaderstats with {' and '.join(s.lower() for s in stats)}", "a leaderboard", f"a {stats[0].lower()} leaderboard",
                       f"leaderstats that start at {start}", "show stats on the player list"])
    body = "\n".join(f'\tlocal {s.lower()} = Instance.new("IntValue")\n\t{s.lower()}.Name = "{s}"\n\t{s.lower()}.Value = {start}\n\t{s.lower()}.Parent = leaderstats\n'
                     for s in stats)
    src = f"""{SERVER}leaderstats
local Players = game:GetService("Players")

Players.PlayerAdded:Connect(function(player)
	local leaderstats = Instance.new("Folder")
	leaderstats.Name = "leaderstats"
	leaderstats.Parent = player

{body}end)
"""
    return core, src


def datastore_simple(rng):
    stat = rng.choice(STATS)
    store = rng.choice(["PlayerData", "SaveData", "GameData_v1", "Stats"])
    core = rng.choice([f"save {stat.lower()} with datastore", "a datastore that saves player data", f"save and load {stat.lower()} when players leave",
                       "a data save system", f"save leaderstats {stat.lower()}"])
    src = f"""{SERVER}save/load {stat} with DataStoreService
local Players = game:GetService("Players")
local DataStoreService = game:GetService("DataStoreService")

local store = DataStoreService:GetDataStore("{store}")
local STAT_NAME = "{stat}"
local MAX_RETRIES = 3

local function withRetry(callback)
	for attempt = 1, MAX_RETRIES do
		local ok, result = pcall(callback)
		if ok then
			return true, result
		end
		warn("DataStore attempt " .. attempt .. " failed: " .. tostring(result))
		task.wait(attempt * 2)
	end
	return false, nil
end

local function load(player)
	local leaderstats = Instance.new("Folder")
	leaderstats.Name = "leaderstats"
	leaderstats.Parent = player

	local stat = Instance.new("IntValue")
	stat.Name = STAT_NAME
	stat.Parent = leaderstats

	local ok, saved = withRetry(function()
		return store:GetAsync("player_" .. player.UserId)
	end)
	if ok and typeof(saved) == "number" then
		stat.Value = saved
	end
end

local function save(player)
	local leaderstats = player:FindFirstChild("leaderstats")
	local stat = leaderstats and leaderstats:FindFirstChild(STAT_NAME)
	if not stat then
		return
	end
	withRetry(function()
		return store:SetAsync("player_" .. player.UserId, stat.Value)
	end)
end

Players.PlayerAdded:Connect(load)
Players.PlayerRemoving:Connect(save)

game:BindToClose(function()
	for _, player in Players:GetPlayers() do
		save(player)
	end
end)
"""
    return core, src


def spinning_part(rng):
    name = rng.choice(["Spinner", "SpinningPart", "Windmill", "Fan", "RotatingBar"])
    speed = rng.choice([30, 45, 60, 90, 120, 180])
    cname, rgb = _rgb(rng)
    use_tween = rng.random() < 0.4
    core = rng.choice(["a spinning part", f"a {cname} part that spins", f"a rotating bar that spins {speed} degrees per second",
                       "a spinner obstacle", "make a part rotate forever"])
    src = SERVER + "spinning part\n"
    src += _part_block("part", name, (rng.choice([12, 16, 20]), 1, 2), (0, 3, 0), rgb, "Neon")
    if use_tween:
        src += f"""
local TweenService = game:GetService("TweenService")

local SECONDS_PER_TURN = {round(360 / speed, 2)}
local info = TweenInfo.new(SECONDS_PER_TURN / 3, Enum.EasingStyle.Linear)
local base = part.CFrame
local step = 0

while true do
	step += 1
	local goal = {{ CFrame = base * CFrame.Angles(0, math.rad(120 * step), 0) }}
	local tween = TweenService:Create(part, info, goal)
	tween:Play()
	tween.Completed:Wait()
end
"""
    else:
        src += f"""
local RunService = game:GetService("RunService")

local DEGREES_PER_SECOND = {speed}

RunService.Heartbeat:Connect(function(dt)
	part.CFrame = part.CFrame * CFrame.Angles(0, math.rad(DEGREES_PER_SECOND * dt), 0)
end)
"""
    return core, src


def click_door(rng):
    name = rng.choice(["Door", "Gate", "SlidingDoor", "VaultDoor"])
    dur = rng.choice([0.5, 0.8, 1, 1.5])
    auto = rng.choice([0, 3, 5])
    use_prompt = rng.random() < 0.5
    core = rng.choice(["a door that opens when clicked", "a sliding door", "a door with a proximity prompt" if use_prompt else "a clickable door",
                       f"a {name.lower()} that opens and closes", f"a door that closes after {auto} seconds" if auto else "a toggle door"])
    src = SERVER + "door that slides open\n"
    src += _part_block("door", name, (6, 10, 1), (0, 5, 0), (124, 92, 70), "Wood")
    src += f"""
local TweenService = game:GetService("TweenService")

local OPEN_TIME = {dur}
local AUTO_CLOSE = {auto}
local closedCFrame = door.CFrame
local openCFrame = closedCFrame * CFrame.new(door.Size.X, 0, 0)
local info = TweenInfo.new(OPEN_TIME, Enum.EasingStyle.Quad, Enum.EasingDirection.Out)
local isOpen = false
local busy = false

local function setOpen(open)
	if busy or open == isOpen then
		return
	end
	busy = true
	isOpen = open
	local tween = TweenService:Create(door, info, {{ CFrame = if open then openCFrame else closedCFrame }})
	tween:Play()
	tween.Completed:Wait()
	busy = false
	if open and AUTO_CLOSE > 0 then
		task.delay(AUTO_CLOSE, function()
			setOpen(false)
		end)
	end
end

"""
    if use_prompt:
        src += """local prompt = Instance.new("ProximityPrompt")
prompt.ActionText = "Open"
prompt.ObjectText = door.Name
prompt.HoldDuration = 0
prompt.Parent = door

prompt.Triggered:Connect(function()
	setOpen(not isOpen)
	prompt.ActionText = if isOpen then "Close" else "Open"
end)
"""
    else:
        src += """local detector = Instance.new("ClickDetector")
detector.MaxActivationDistance = 16
detector.Parent = door

detector.MouseClick:Connect(function()
	setOpen(not isOpen)
end)
"""
    return core, src


def chest_prompt(rng):
    stat = rng.choice(STATS[:6])
    reward = rng.choice([10, 25, 50, 100, 250])
    cd = rng.choice([30, 60, 120, 300])
    core = rng.choice(["a treasure chest that gives coins", f"a chest that gives {reward} {stat.lower()}", "a proximity prompt reward chest",
                       f"a chest you can open every {cd} seconds", "a daily reward chest"])
    src = SERVER + "reward chest with a ProximityPrompt\n"
    src += _part_block("chest", "Chest", (4, 3, 3), (10, 1.5, 0), (140, 90, 40), "Wood")
    src += f"""
local Players = game:GetService("Players")

local STAT_NAME = "{stat}"
local REWARD = {reward}
local COOLDOWN = {cd}
local lastOpened = {{}}

local prompt = Instance.new("ProximityPrompt")
prompt.ActionText = "Open chest"
prompt.HoldDuration = 1
prompt.Parent = chest

prompt.Triggered:Connect(function(player)
	local now = os.time()
	local last = lastOpened[player.UserId]
	if last and now - last < COOLDOWN then
		return
	end
	local leaderstats = player:FindFirstChild("leaderstats")
	local stat = leaderstats and leaderstats:FindFirstChild(STAT_NAME)
	if not stat then
		return
	end
	lastOpened[player.UserId] = now
	stat.Value += REWARD
end)

Players.PlayerRemoving:Connect(function(player)
	lastOpened[player.UserId] = nil
end)
"""
    return core, src


def heal_pad(rng):
    amount = rng.choice([5, 10, 20, 25])
    interval = rng.choice([0.5, 1])
    core = rng.choice(["a healing pad", f"a part that heals {amount} health", "a heal zone", "a regen pad that heals players standing on it"])
    src = SERVER + "healing pad\n"
    src += _part_block("pad", "HealPad", (8, 1, 8), (-12, 0.5, 0), (0, 200, 70), "Neon")
    src += f"""
local HEAL_AMOUNT = {amount}
local INTERVAL = {interval}
local lastHeal = {{}}

pad.Touched:Connect(function(hit)
	local humanoid = hit.Parent and hit.Parent:FindFirstChildOfClass("Humanoid")
	if not humanoid or humanoid.Health <= 0 then
		return
	end
	local now = os.clock()
	if lastHeal[humanoid] and now - lastHeal[humanoid] < INTERVAL then
		return
	end
	lastHeal[humanoid] = now
	humanoid.Health = math.min(humanoid.MaxHealth, humanoid.Health + HEAL_AMOUNT)
end)
"""
    return core, src


def disappearing_platforms(rng):
    count = rng.choice([4, 6, 8, 10])
    fade = rng.choice([0.5, 1, 1.5])
    back = rng.choice([2, 3, 5])
    core = rng.choice(["disappearing platforms", f"{count} platforms that vanish when touched", "fading platforms for an obby",
                       f"platforms that disappear after {fade} seconds and come back"])
    src = f"""{SERVER}disappearing platforms
local Players = game:GetService("Players")

local COUNT = {count}
local FADE_TIME = {fade}
local RESPAWN_TIME = {back}

local folder = Instance.new("Folder")
folder.Name = "FadingPlatforms"
folder.Parent = workspace

local function setup(platform)
	local fading = false
	platform.Touched:Connect(function(hit)
		if fading or not Players:GetPlayerFromCharacter(hit.Parent) then
			return
		end
		fading = true
		for i = 1, 10 do
			platform.Transparency = i / 10
			task.wait(FADE_TIME / 10)
		end
		platform.CanCollide = false
		task.wait(RESPAWN_TIME)
		platform.CanCollide = true
		platform.Transparency = 0
		fading = false
	end)
end

for i = 1, COUNT do
	local platform = Instance.new("Part")
	platform.Name = "Platform" .. i
	platform.Size = Vector3.new(6, 1, 6)
	platform.Position = Vector3.new(i * 9, 5 + i, 0)
	platform.Anchored = true
	platform.Color = Color3.fromRGB(255, 255, 255)
	platform.Parent = folder
	setup(platform)
end
"""
    return core, src


def jump_pad(rng):
    power = rng.choice([80, 100, 120, 150, 200])
    cname, rgb = _rgb(rng)
    core = rng.choice(["a jump pad", f"a {cname} bounce pad", f"a launch pad with power {power}", "a trampoline part"])
    src = SERVER + "jump pad\n"
    src += _part_block("pad", "JumpPad", (6, 1, 6), (20, 0.5, 0), rgb, "Neon")
    src += f"""
local LAUNCH_POWER = {power}
local lastLaunch = {{}}

pad.Touched:Connect(function(hit)
	local character = hit.Parent
	local root = character and character:FindFirstChild("HumanoidRootPart")
	if not root or not character:FindFirstChildOfClass("Humanoid") then
		return
	end
	local now = os.clock()
	if lastLaunch[character] and now - lastLaunch[character] < 0.5 then
		return
	end
	lastLaunch[character] = now
	local v = root.AssemblyLinearVelocity
	root.AssemblyLinearVelocity = Vector3.new(v.X, LAUNCH_POWER, v.Z)
end)
"""
    return core, src


def speed_pad(rng):
    speed = rng.choice([32, 40, 50, 60])
    dur = rng.choice([3, 5, 8, 10])
    core = rng.choice(["a speed boost pad", f"a pad that makes you fast for {dur} seconds", "a speed boost", f"a speed pad that sets speed to {speed}"])
    src = SERVER + "speed boost pad\n"
    src += _part_block("pad", "SpeedPad", (6, 1, 6), (-20, 0.5, 0), (255, 210, 0), "Neon")
    src += f"""
local BOOST_SPEED = {speed}
local DURATION = {dur}
local boosted = {{}}

pad.Touched:Connect(function(hit)
	local humanoid = hit.Parent and hit.Parent:FindFirstChildOfClass("Humanoid")
	if not humanoid or boosted[humanoid] then
		return
	end
	boosted[humanoid] = true
	local normal = humanoid.WalkSpeed
	humanoid.WalkSpeed = BOOST_SPEED
	task.delay(DURATION, function()
		if humanoid.Parent then
			humanoid.WalkSpeed = normal
		end
		boosted[humanoid] = nil
	end)
end)
"""
    return core, src


def countdown_gui(rng):
    seconds = rng.choice([10, 30, 60, 90, 120])
    text = rng.choice(["Time left", "Countdown", "Timer", "Round ends in"])
    core = rng.choice(["a countdown timer gui", f"a {seconds} second timer on screen", "a timer ui", "show a countdown on the screen"])
    src = f"""{LOCAL_PLAYER}on-screen countdown
local Players = game:GetService("Players")

local player = Players.LocalPlayer
local SECONDS = {seconds}

local gui = Instance.new("ScreenGui")
gui.Name = "CountdownGui"
gui.ResetOnSpawn = false
gui.Parent = player:WaitForChild("PlayerGui")

local label = Instance.new("TextLabel")
label.Size = UDim2.new(0, 220, 0, 50)
label.Position = UDim2.new(0.5, -110, 0, 20)
label.BackgroundColor3 = Color3.fromRGB(25, 25, 30)
label.BackgroundTransparency = 0.2
label.TextColor3 = Color3.fromRGB(255, 255, 255)
label.TextScaled = true
label.Font = Enum.Font.GothamBold
label.Parent = gui

local corner = Instance.new("UICorner")
corner.CornerRadius = UDim.new(0, 10)
corner.Parent = label

for remaining = SECONDS, 0, -1 do
	label.Text = string.format("{text}: %d:%02d", remaining // 60, remaining % 60)
	task.wait(1)
end
label.Text = "Time's up!"
"""
    return core, src


def clicker(rng):
    stat = rng.choice(STATS)
    per = rng.choice([1, 2, 5])
    core = rng.choice(["a clicker game", f"a button that gives {stat.lower()} when clicked", "a click to earn system",
                       f"a clicker where each click gives {per} {stat.lower()}"])
    src = f"""{SERVER}clicker (server awards the clicks)
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local STAT_NAME = "{stat}"
local PER_CLICK = {per}
local MAX_CLICKS_PER_SECOND = 10

local remote = Instance.new("RemoteEvent")
remote.Name = "Click"
remote.Parent = ReplicatedStorage

local clicks = {{}}

Players.PlayerAdded:Connect(function(player)
	local leaderstats = Instance.new("Folder")
	leaderstats.Name = "leaderstats"
	leaderstats.Parent = player
	local stat = Instance.new("IntValue")
	stat.Name = STAT_NAME
	stat.Parent = leaderstats
end)

remote.OnServerEvent:Connect(function(player)
	local now = os.clock()
	local entry = clicks[player]
	if not entry or now - entry.start >= 1 then
		entry = {{ start = now, count = 0 }}
		clicks[player] = entry
	end
	entry.count += 1
	if entry.count > MAX_CLICKS_PER_SECOND then
		return
	end
	local stat = player.leaderstats:FindFirstChild(STAT_NAME)
	if stat then
		stat.Value += PER_CLICK
	end
end)

Players.PlayerRemoving:Connect(function(player)
	clicks[player] = nil
end)
"""
    return core, src


def give_tool_on_join(rng):
    tool = rng.choice(["Flashlight", "Pickaxe", "Hammer", "Map", "Torch"])
    core = rng.choice([f"give every player a {tool.lower()} when they join", f"a starter {tool.lower()} tool", "give players a tool on spawn"])
    src = f"""{SERVER}give every player a {tool}
local Players = game:GetService("Players")

local TOOL_NAME = "{tool}"

local function makeTool()
	local tool = Instance.new("Tool")
	tool.Name = TOOL_NAME
	local handle = Instance.new("Part")
	handle.Name = "Handle"
	handle.Size = Vector3.new(1, 1, 3)
	handle.CanCollide = false
	handle.Parent = tool
	return tool
end

Players.PlayerAdded:Connect(function(player)
	player.CharacterAdded:Connect(function()
		local backpack = player:WaitForChild("Backpack")
		if not backpack:FindFirstChild(TOOL_NAME) then
			makeTool().Parent = backpack
		end
	end)
end)
"""
    return core, src


def chat_commands(rng):
    prefix = rng.choice(["/", "!", ";", ":"])
    core = rng.choice(["admin commands", f"chat commands with {prefix} prefix", "a heal command for admins", "simple admin commands like speed and heal"])
    src = f"""{SERVER}chat admin commands
local Players = game:GetService("Players")

local PREFIX = "{prefix}"
local ADMINS = {{
	[game.CreatorId] = true,
}}

local commands = {{}}

commands.heal = function(player)
	local humanoid = player.Character and player.Character:FindFirstChildOfClass("Humanoid")
	if humanoid then
		humanoid.Health = humanoid.MaxHealth
	end
end

commands.speed = function(player, value)
	local humanoid = player.Character and player.Character:FindFirstChildOfClass("Humanoid")
	local speed = tonumber(value)
	if humanoid and speed then
		humanoid.WalkSpeed = math.clamp(speed, 0, 100)
	end
end

commands.reset = function(player)
	player:LoadCharacter()
end

Players.PlayerAdded:Connect(function(player)
	player.Chatted:Connect(function(message)
		if not ADMINS[player.UserId] or string.sub(message, 1, #PREFIX) ~= PREFIX then
			return
		end
		local args = string.split(string.sub(message, #PREFIX + 1), " ")
		local name = string.lower(table.remove(args, 1) or "")
		local command = commands[name]
		if command then
			command(player, table.unpack(args))
		end
	end)
end)
"""
    return core, src


def music_player(rng):
    vol = rng.choice([0.3, 0.5, 0.7])
    core = rng.choice(["background music", "a music playlist", "loop music in the game", "a music player script"])
    src = f"""{SERVER}background music playlist
local SoundService = game:GetService("SoundService")

-- Replace these with your own audio asset ids.
local PLAYLIST = {{
	"rbxassetid://0",
	"rbxassetid://0",
}}
local VOLUME = {vol}

local sound = Instance.new("Sound")
sound.Name = "BackgroundMusic"
sound.Volume = VOLUME
sound.Parent = SoundService

local index = 0
while true do
	index = index % #PLAYLIST + 1
	sound.SoundId = PLAYLIST[index]
	sound:Play()
	if sound.TimeLength > 0 then
		sound.Ended:Wait()
	else
		task.wait(5)
	end
end
"""
    return core, src


def falling_parts(rng):
    every = rng.choice([0.5, 1, 2])
    life = rng.choice([5, 8, 10])
    core = rng.choice(["raining parts", "parts falling from the sky", f"spawn a random part every {every} seconds", "a meteor shower"])
    src = f"""{SERVER}falling parts
local Debris = game:GetService("Debris")

local SPAWN_EVERY = {every}
local LIFETIME = {life}
local AREA = 60

while true do
	local part = Instance.new("Part")
	part.Size = Vector3.new(2, 2, 2) * math.random(1, 3)
	part.Position = Vector3.new(math.random(-AREA, AREA), 80, math.random(-AREA, AREA))
	part.Color = Color3.fromHSV(math.random(), 0.8, 1)
	part.Material = Enum.Material.Neon
	part.Parent = workspace
	Debris:AddItem(part, LIFETIME)
	task.wait(SPAWN_EVERY)
end
"""
    return core, src


def npc_follow(rng):
    rng_dist = rng.choice([30, 50, 80])
    core = rng.choice(["an npc that follows the player", "a zombie that chases players", f"an enemy that follows players within {rng_dist} studs",
                       "make an npc chase the closest player"])
    src = f"""{SERVER}NPC that chases the nearest player
local Players = game:GetService("Players")

local DETECT_RANGE = {rng_dist}
local npc = workspace:FindFirstChild("Zombie")
if not npc then
	warn("Put an R15/R6 character model named 'Zombie' in Workspace (with a Humanoid and HumanoidRootPart).")
	return
end
local humanoid = npc:WaitForChild("Humanoid")
local root = npc:WaitForChild("HumanoidRootPart")

local function nearestCharacter()
	local best, bestDistance = nil, DETECT_RANGE
	for _, player in Players:GetPlayers() do
		local character = player.Character
		local target = character and character:FindFirstChild("HumanoidRootPart")
		local targetHumanoid = character and character:FindFirstChildOfClass("Humanoid")
		if target and targetHumanoid and targetHumanoid.Health > 0 then
			local distance = (target.Position - root.Position).Magnitude
			if distance < bestDistance then
				best, bestDistance = target, distance
			end
		end
	end
	return best
end

while humanoid.Health > 0 do
	local target = nearestCharacter()
	if target then
		humanoid:MoveTo(target.Position)
	end
	task.wait(0.25)
end
"""
    return core, src


def name_tag(rng):
    cname, rgb = _rgb(rng)
    core = rng.choice(["an overhead name tag", "a billboard gui above players heads", f"a {cname} name tag over players", "show player names above their heads"])
    src = f"""{SERVER}overhead name tags
local Players = game:GetService("Players")

local function addTag(player, character)
	local head = character:WaitForChild("Head")
	local billboard = Instance.new("BillboardGui")
	billboard.Name = "NameTag"
	billboard.Size = UDim2.new(0, 200, 0, 50)
	billboard.StudsOffset = Vector3.new(0, 2.5, 0)
	billboard.AlwaysOnTop = true
	billboard.Adornee = head
	billboard.Parent = head

	local label = Instance.new("TextLabel")
	label.Size = UDim2.new(1, 0, 1, 0)
	label.BackgroundTransparency = 1
	label.Text = player.DisplayName
	label.TextColor3 = {_c3(rgb)}
	label.TextStrokeTransparency = 0.5
	label.TextScaled = true
	label.Font = Enum.Font.GothamBold
	label.Parent = billboard
end

Players.PlayerAdded:Connect(function(player)
	player.CharacterAdded:Connect(function(character)
		addTag(player, character)
	end)
end)
"""
    return core, src


def kill_tracker(rng):
    core = rng.choice(["a kills leaderboard", "track kills and deaths", "give kills to the player who killed someone", "kills and deaths leaderstats"])
    src = f"""{SERVER}kills and deaths tracking
local Players = game:GetService("Players")

-- Weapons should tag the victim's Humanoid with an ObjectValue named "creator" pointing at the attacker.
Players.PlayerAdded:Connect(function(player)
	local leaderstats = Instance.new("Folder")
	leaderstats.Name = "leaderstats"
	leaderstats.Parent = player

	local kills = Instance.new("IntValue")
	kills.Name = "Kills"
	kills.Parent = leaderstats

	local deaths = Instance.new("IntValue")
	deaths.Name = "Deaths"
	deaths.Parent = leaderstats

	player.CharacterAdded:Connect(function(character)
		local humanoid = character:WaitForChild("Humanoid")
		humanoid.Died:Connect(function()
			deaths.Value += 1
			local tag = humanoid:FindFirstChild("creator")
			local killer = tag and tag.Value
			if killer and killer:IsA("Player") and killer ~= player then
				local stats = killer:FindFirstChild("leaderstats")
				local killerKills = stats and stats:FindFirstChild("Kills")
				if killerKills then
					killerKills.Value += 1
				end
			end
		end)
	end)
end)
"""
    return core, src


def checkpoints(rng):
    count = rng.choice([5, 8, 10, 15])
    core = rng.choice(["checkpoints for an obby", f"{count} checkpoints that save your stage", "a stage checkpoint system", "obby checkpoints with a stage leaderstat"])
    src = f"""{SERVER}obby checkpoints
local Players = game:GetService("Players")

local STAGE_COUNT = {count}
local folder = workspace:FindFirstChild("Checkpoints")
if not folder then
	folder = Instance.new("Folder")
	folder.Name = "Checkpoints"
	folder.Parent = workspace
	for i = 1, STAGE_COUNT do
		local pad = Instance.new("Part")
		pad.Name = tostring(i)
		pad.Size = Vector3.new(8, 1, 8)
		pad.Position = Vector3.new(i * 20, 0.5 + i * 2, 0)
		pad.Anchored = true
		pad.Color = Color3.fromRGB(0, 200, 70)
		pad.Parent = folder
	end
end

Players.PlayerAdded:Connect(function(player)
	local leaderstats = Instance.new("Folder")
	leaderstats.Name = "leaderstats"
	leaderstats.Parent = player
	local stage = Instance.new("IntValue")
	stage.Name = "Stage"
	stage.Value = 1
	stage.Parent = leaderstats

	player.CharacterAdded:Connect(function(character)
		local root = character:WaitForChild("HumanoidRootPart")
		local pad = folder:FindFirstChild(tostring(stage.Value))
		if pad then
			task.wait()
			root.CFrame = pad.CFrame + Vector3.new(0, 4, 0)
		end
	end)
end)

for _, pad in folder:GetChildren() do
	local number = tonumber(pad.Name)
	if number and pad:IsA("BasePart") then
		pad.Touched:Connect(function(hit)
			local player = Players:GetPlayerFromCharacter(hit.Parent)
			local stage = player and player:FindFirstChild("leaderstats") and player.leaderstats:FindFirstChild("Stage")
			if stage and number == stage.Value + 1 then
				stage.Value = number
			end
		end)
	end
end
"""
    return core, src


def play_animation(rng):
    key = rng.choice(KEYS[1:])
    core = rng.choice([f"play an animation when pressing {key}", "play a custom animation on key press", f"an emote on the {key} key",
                       "load and play an animation on the character"])
    src = f"""{LOCAL_CHAR}play an animation on a key
local UserInputService = game:GetService("UserInputService")

local character = script.Parent
local humanoid = character:WaitForChild("Humanoid")
local animator = humanoid:WaitForChild("Animator")

-- Replace with your published animation id.
local ANIMATION_ID = "rbxassetid://0"
local KEY = Enum.KeyCode.{key}

local animation = Instance.new("Animation")
animation.AnimationId = ANIMATION_ID
local track = animator:LoadAnimation(animation)
track.Priority = Enum.AnimationPriority.Action

UserInputService.InputBegan:Connect(function(input, processed)
	if processed or input.KeyCode ~= KEY then
		return
	end
	if track.IsPlaying then
		track:Stop(0.2)
	else
		track:Play(0.2)
	end
end)
"""
    return core, src


def camera_shake(rng):
    core = rng.choice(["camera shake", "shake the camera", "a screen shake effect", "a camera shake function"])
    intensity = rng.choice([0.3, 0.5, 1])
    src = f"""{LOCAL_PLAYER}camera shake
local RunService = game:GetService("RunService")

local camera = workspace.CurrentCamera
local INTENSITY = {intensity}

local function shake(duration, intensity)
	local finish = os.clock() + duration
	local connection
	connection = RunService.RenderStepped:Connect(function()
		local remaining = finish - os.clock()
		if remaining <= 0 then
			connection:Disconnect()
			return
		end
		local strength = intensity * (remaining / duration)
		local offset = Vector3.new(math.random() - 0.5, math.random() - 0.5, 0) * strength
		camera.CFrame = camera.CFrame * CFrame.new(offset)
	end)
end

shake(0.5, INTENSITY)
"""
    return core, src


def util_module(rng):
    core = rng.choice(["a utility module", "a module with helper functions", "a module script to format numbers", "a number abbreviation module"])
    src = f"""{MODULE}utility functions
local Util = {{}}

local SUFFIXES = {{ "", "K", "M", "B", "T" }}

function Util.abbreviate(n: number): string
	local i = 1
	while math.abs(n) >= 1000 and i < #SUFFIXES do
		n /= 1000
		i += 1
	end
	if i == 1 then
		return tostring(math.floor(n))
	end
	return string.format("%.1f%s", n, SUFFIXES[i])
end

function Util.formatTime(seconds: number): string
	seconds = math.max(0, math.floor(seconds))
	return string.format("%d:%02d", seconds // 60, seconds % 60)
end

function Util.shuffle(list: {{any}})
	for i = #list, 2, -1 do
		local j = math.random(i)
		list[i], list[j] = list[j], list[i]
	end
	return list
end

function Util.clamp01(x: number): number
	return math.clamp(x, 0, 1)
end

return Util
"""
    return core, src


def build_obby_script(rng):
    count = rng.choice([5, 8, 10, 12, 15, 20])
    gap = rng.choice([8, 10, 12])
    rise = rng.choice([0, 1, 2])
    cname, rgb = _rgb(rng)
    core = rng.choice([f"an obby with {count} platforms", "build an obby with a script", f"a {cname} obby", f"generate {count} parkour platforms",
                       "a script that builds an obby"])
    src = f"""{SERVER}builds a {count}-platform obby
local COUNT = {count}
local GAP = {gap}
local RISE = {rise}

local model = Instance.new("Model")
model.Name = "Obby"

for i = 1, COUNT do
	local platform = Instance.new("Part")
	platform.Name = "Platform" .. i
	platform.Size = Vector3.new(6, 1, 6)
	platform.Position = Vector3.new(i * GAP, 5 + i * RISE, math.sin(i) * 6)
	platform.Anchored = true
	platform.Color = {_c3(rgb)}
	platform.Material = Enum.Material.SmoothPlastic
	platform.Parent = model
end

local finish = Instance.new("Part")
finish.Name = "Finish"
finish.Size = Vector3.new(10, 1, 10)
finish.Position = Vector3.new((COUNT + 1) * GAP, 5 + (COUNT + 1) * RISE, 0)
finish.Anchored = true
finish.Color = Color3.fromRGB(255, 215, 0)
finish.Material = Enum.Material.Neon
finish.Parent = model

model.Parent = workspace
"""
    return core, src


def build_house_script(rng):
    w = rng.choice([16, 20, 24, 30])
    d = rng.choice([16, 20, 24])
    h = rng.choice([10, 12, 14])
    mat = rng.choice(["Brick", "Wood", "WoodPlanks", "Concrete", "Cobblestone"])
    core = rng.choice(["build a house with a script", f"a {mat.lower()} house", f"a script that makes a {w} by {d} house", "generate a small house"])
    src = f"""{SERVER}builds a simple house
local WIDTH, DEPTH, HEIGHT = {w}, {d}, {h}
local THICK = 1
local ORIGIN = Vector3.new(0, 0, 40)

local house = Instance.new("Model")
house.Name = "House"

local function block(name, size, offset, color, material)
	local part = Instance.new("Part")
	part.Name = name
	part.Size = size
	part.Position = ORIGIN + offset
	part.Anchored = true
	part.Color = color
	part.Material = material
	part.Parent = house
	return part
end

local wallColor = Color3.fromRGB(200, 170, 140)
local wallMaterial = Enum.Material.{mat}

block("Floor", Vector3.new(WIDTH, THICK, DEPTH), Vector3.new(0, THICK / 2, 0), Color3.fromRGB(120, 90, 60), Enum.Material.WoodPlanks)
block("BackWall", Vector3.new(WIDTH, HEIGHT, THICK), Vector3.new(0, HEIGHT / 2, -DEPTH / 2), wallColor, wallMaterial)
block("LeftWall", Vector3.new(THICK, HEIGHT, DEPTH), Vector3.new(-WIDTH / 2, HEIGHT / 2, 0), wallColor, wallMaterial)
block("RightWall", Vector3.new(THICK, HEIGHT, DEPTH), Vector3.new(WIDTH / 2, HEIGHT / 2, 0), wallColor, wallMaterial)

local doorWidth = 4
local side = (WIDTH - doorWidth) / 2
block("FrontLeft", Vector3.new(side, HEIGHT, THICK), Vector3.new(-(doorWidth + side) / 2, HEIGHT / 2, DEPTH / 2), wallColor, wallMaterial)
block("FrontRight", Vector3.new(side, HEIGHT, THICK), Vector3.new((doorWidth + side) / 2, HEIGHT / 2, DEPTH / 2), wallColor, wallMaterial)
block("AboveDoor", Vector3.new(doorWidth, HEIGHT - 7, THICK), Vector3.new(0, 7 + (HEIGHT - 7) / 2, DEPTH / 2), wallColor, wallMaterial)
block("Roof", Vector3.new(WIDTH + 2, THICK, DEPTH + 2), Vector3.new(0, HEIGHT + THICK / 2, 0), Color3.fromRGB(110, 40, 40), Enum.Material.Slate)

house.Parent = workspace
"""
    return core, src


def build_tower_script(rng):
    floors = rng.choice([3, 4, 5, 6, 8, 10])
    fh = rng.choice([10, 12])
    core = rng.choice([f"a tower with {floors} floors", "build a tower with a script", f"a {floors} story building", "a tall tower"])
    src = f"""{SERVER}builds a {floors}-floor tower
local FLOORS = {floors}
local FLOOR_HEIGHT = {fh}
local SIZE = 20
local ORIGIN = Vector3.new(-50, 0, 0)

local tower = Instance.new("Model")
tower.Name = "Tower"

for level = 0, FLOORS - 1 do
	local y = level * FLOOR_HEIGHT
	local floor = Instance.new("Part")
	floor.Name = "Floor" .. (level + 1)
	floor.Size = Vector3.new(SIZE, 1, SIZE)
	floor.Position = ORIGIN + Vector3.new(0, y + 0.5, 0)
	floor.Anchored = true
	floor.Material = Enum.Material.Concrete
	floor.Parent = tower

	for _, corner in {{ Vector3.new(1, 0, 1), Vector3.new(-1, 0, 1), Vector3.new(1, 0, -1), Vector3.new(-1, 0, -1) }} do
		local pillar = Instance.new("Part")
		pillar.Name = "Pillar"
		pillar.Size = Vector3.new(1, FLOOR_HEIGHT, 1)
		pillar.Position = ORIGIN + corner * (SIZE / 2 - 0.5) + Vector3.new(0, y + FLOOR_HEIGHT / 2, 0)
		pillar.Anchored = true
		pillar.Material = Enum.Material.Concrete
		pillar.Parent = tower
	end
end

tower.Parent = workspace
"""
    return core, src


def stats_gui(rng):
    stat = rng.choice(STATS)
    core = rng.choice([f"a gui that shows my {stat.lower()}", "a currency display ui", f"show {stat.lower()} on the screen", "a hud for coins"])
    src = f"""{LOCAL_PLAYER}on-screen {stat} counter
local Players = game:GetService("Players")

local player = Players.LocalPlayer
local STAT_NAME = "{stat}"

local gui = Instance.new("ScreenGui")
gui.Name = "StatDisplay"
gui.ResetOnSpawn = false
gui.Parent = player:WaitForChild("PlayerGui")

local label = Instance.new("TextLabel")
label.Size = UDim2.new(0, 200, 0, 44)
label.Position = UDim2.new(0, 16, 1, -60)
label.BackgroundColor3 = Color3.fromRGB(30, 30, 36)
label.TextColor3 = Color3.fromRGB(255, 220, 90)
label.Font = Enum.Font.GothamBold
label.TextScaled = true
label.Text = STAT_NAME .. ": 0"
label.Parent = gui

local corner = Instance.new("UICorner")
corner.CornerRadius = UDim.new(0, 8)
corner.Parent = label

local leaderstats = player:WaitForChild("leaderstats")
local stat = leaderstats:WaitForChild(STAT_NAME)

local function refresh()
	label.Text = STAT_NAME .. ": " .. tostring(stat.Value)
end

stat.Changed:Connect(refresh)
refresh()
"""
    return core, src


FAMILIES: dict[str, Callable] = {f.__name__: f for f in [
    kill_brick, coin_pickup, teleporter, sprint, double_jump, day_night, round_system, sword_tool, shop,
    leaderstats_simple, datastore_simple, spinning_part, click_door, chest_prompt, heal_pad, disappearing_platforms,
    jump_pad, speed_pad, countdown_gui, clicker, give_tool_on_join, chat_commands, music_player, falling_parts,
    npc_follow, name_tag, kill_tracker, checkpoints, play_animation, camera_shake, util_module, build_obby_script,
    build_house_script, build_tower_script, stats_gui]}

# Families never trained on: used to measure generalisation honestly.
HELD_OUT_FAMILIES = ("speed_pad", "name_tag", "camera_shake")


def verify_solution(src: str) -> bool:
    return check_structure(src).ok and not [f for f in lint(src) if f.severity == "error"] and not lint(src)


# ---- French / Spanish phrasing (templates translated once into i18n_cores.json) -------------
ASK_I18N = {"fr": ["", "", "fais ", "fais-moi ", "crée ", "écris ", "je veux ", "j'ai besoin d'", "tu peux faire ",
                   "code ", "comment faire ", "ajoute ", "roblox : "],
            "es": ["", "", "haz ", "hazme ", "crea ", "escribe ", "quiero ", "necesito ", "puedes hacer ", "programa ",
                   "cómo hago ", "añade ", "roblox: "]}
TAIL_I18N = {"fr": ["", "", "", " dans roblox", " pour mon jeu", " stp", " svp", " (script complet)", " avec des commentaires"],
             "es": ["", "", "", " en roblox", " para mi juego", " por favor", " porfa", " (script completo)", " con comentarios"]}
COLOR_I18N = {"red": ("rouge", "rojo"), "green": ("vert", "verde"), "blue": ("bleu", "azul"), "yellow": ("jaune", "amarillo"),
              "purple": ("violet", "morado"), "orange": ("orange", "naranja"), "white": ("blanc", "blanco"),
              "black": ("noir", "negro"), "pink": ("rose", "rosa"), "cyan": ("cyan", "cian")}
_SLOT = re.compile(r"\d+(?:\.\d+)?|\b(?:%s)\b" % "|".join(
    map(re.escape, sorted(list(COLORS) + KEYS + STATS, key=len, reverse=True))), re.I)
_I18N: dict | None = None


def _i18n() -> dict:
    global _I18N
    if _I18N is None:
        import json
        from pathlib import Path
        f = Path(__file__).with_name("i18n_cores.json")
        _I18N = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return _I18N


def translate_core(core: str, lang: str, rng: random.Random) -> str | None:
    """English core -> French/Spanish, keeping the exact numbers / colours / keys / stat names. None if untranslated."""
    vals: list[str] = []

    def take(m):
        vals.append(m.group(0))
        return "{%d}" % (len(vals) - 1)

    entry = _i18n().get(_SLOT.sub(take, core))
    if not entry or not entry.get(lang):
        return None
    li = 0 if lang == "fr" else 1
    out = rng.choice(entry[lang])
    for i, v in enumerate(vals):
        v = COLOR_I18N.get(v.lower(), (v, v))[li] if v.lower() in COLOR_I18N else v
        out = out.replace("{%d}" % i, v)
    return out


def _phrase_lang(rng: random.Random, core: str, lang: str) -> str:
    p = rng.choice(ASK_I18N[lang]) + core + rng.choice(TAIL_I18N[lang])
    p = re.sub(r"\s+", " ", p).strip()
    if lang == "fr":  # elision: "besoin d'" only before a vowel, "de" + vowel -> "d'"
        p = re.sub(r"\bd'(?=[^aeiouyhéèêàâîôû])", "de ", p, flags=re.I)
        p = re.sub(r"\bde (de |d')", r"\1", p)
        p = re.sub(r"\bde (?=[aeiouyéèêàâîôû])", "d'", p, flags=re.I)
    if rng.random() < 0.3:
        p = p[0].upper() + p[1:]
    if rng.random() < 0.15:
        p += rng.choice(["?", ".", "!"])
    return p


def sample(family: str, rng: random.Random, lang: str = "en") -> ScriptSample:
    core, src = FAMILIES[family](rng)
    if lang != "en":
        t = translate_core(core, lang, rng)
        if t:
            return ScriptSample(family, _phrase_lang(rng, t, lang), src)
    return ScriptSample(family, _phrase(rng, core), src)


def generate(n_per_family: int, seed: int = 0, families: list[str] | None = None,
             langs: tuple[str, ...] = ("en",)) -> tuple[list[ScriptSample], int]:
    """Verified samples (deduplicated on prompt+solution) and the number rejected by the verifier.

    With several ``langs`` each sample's prompt language is drawn uniformly (FR/ES fall back to English
    for a template that has no translation)."""
    rng = random.Random(seed)
    out, rejected, seen = [], 0, set()
    verified_cache: dict[str, bool] = {}
    for fam in families or list(FAMILIES):
        for _ in range(n_per_family):
            s = sample(fam, rng, rng.choice(langs) if len(langs) > 1 else langs[0])
            key = (s.prompt, s.solution)
            if key in seen:
                continue
            seen.add(key)
            ok = verified_cache.get(s.solution)
            if ok is None:
                ok = verified_cache[s.solution] = verify_solution(s.solution)
            if ok:
                out.append(s)
            else:
                rejected += 1
    return out, rejected
