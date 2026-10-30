--!strict
-- NPC that patrols waypoints using PathfindingService and recomputes when blocked.

local PathfindingService = game:GetService("PathfindingService")

local npc = script.Parent :: Model
local humanoid = npc:WaitForChild("Humanoid") :: Humanoid
local root = npc:WaitForChild("HumanoidRootPart") :: BasePart
local waypointsFolder = workspace:WaitForChild("PatrolPoints")

local path = PathfindingService:CreatePath({
	AgentRadius = 2,
	AgentHeight = 5,
	AgentCanJump = true,
})

local function walkTo(goal: Vector3): boolean
	local ok = pcall(function()
		path:ComputeAsync(root.Position, goal)
	end)
	if not ok or path.Status ~= Enum.PathStatus.Success then
		return false
	end
	local blocked = false
	local blockedConnection = path.Blocked:Connect(function()
		blocked = true
	end)
	for _, waypoint in path:GetWaypoints() do
		if blocked or humanoid.Health <= 0 then
			break
		end
		if waypoint.Action == Enum.PathWaypointAction.Jump then
			humanoid.Jump = true
		end
		humanoid:MoveTo(waypoint.Position)
		local reached = humanoid.MoveToFinished:Wait()
		if not reached then
			break
		end
	end
	blockedConnection:Disconnect()
	return not blocked
end

while humanoid.Health > 0 do
	for _, point in waypointsFolder:GetChildren() do
		if point:IsA("BasePart") then
			if not walkTo(point.Position) then
				task.wait(1)
			end
		end
	end
	task.wait(0.5)
end
