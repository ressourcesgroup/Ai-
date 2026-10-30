--!strict
-- Melee hit validation: the server checks cooldown, range and target state.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local ATTACK_COOLDOWN = 0.45
local MAX_RANGE = 8
local DAMAGE = 15

local attackRemote = Instance.new("RemoteEvent")
attackRemote.Name = "MeleeAttack"
attackRemote.Parent = ReplicatedStorage

local lastAttack: { [Player]: number } = {}

Players.PlayerRemoving:Connect(function(player)
	lastAttack[player] = nil
end)

local function getRoot(character: Model?): BasePart?
	if not character then
		return nil
	end
	local root = character:FindFirstChild("HumanoidRootPart")
	return if root and root:IsA("BasePart") then root else nil
end

attackRemote.OnServerEvent:Connect(function(attacker: Player, target: unknown)
	if typeof(target) ~= "Instance" or not target:IsA("Model") then
		return
	end
	local now = os.clock()
	if now - (lastAttack[attacker] or 0) < ATTACK_COOLDOWN then
		return
	end
	local humanoid = target:FindFirstChildOfClass("Humanoid")
	if not humanoid or humanoid.Health <= 0 then
		return
	end
	if Players:GetPlayerFromCharacter(target) == attacker then
		return
	end
	local attackerRoot, targetRoot = getRoot(attacker.Character), getRoot(target)
	if not attackerRoot or not targetRoot then
		return
	end
	if (attackerRoot.Position - targetRoot.Position).Magnitude > MAX_RANGE then
		return
	end
	lastAttack[attacker] = now
	humanoid:TakeDamage(DAMAGE)
end)
