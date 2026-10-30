--!strict
-- Responsive health bar that tweens on change.

local Players = game:GetService("Players")
local TweenService = game:GetService("TweenService")

local player = Players.LocalPlayer
local TWEEN_INFO = TweenInfo.new(0.25, Enum.EasingStyle.Quad, Enum.EasingDirection.Out)

local gui = Instance.new("ScreenGui")
gui.Name = "HealthGui"
gui.ResetOnSpawn = false
gui.Parent = player:WaitForChild("PlayerGui")

local frame = Instance.new("Frame")
frame.AnchorPoint = Vector2.new(0.5, 1)
frame.Position = UDim2.fromScale(0.5, 0.95)
frame.Size = UDim2.fromScale(0.3, 0.03)
frame.BackgroundColor3 = Color3.fromRGB(30, 30, 30)
frame.Parent = gui

local corner = Instance.new("UICorner")
corner.CornerRadius = UDim.new(0.5, 0)
corner.Parent = frame

local fill = Instance.new("Frame")
fill.Size = UDim2.fromScale(1, 1)
fill.BackgroundColor3 = Color3.fromRGB(80, 220, 100)
fill.Parent = frame
corner:Clone().Parent = fill

local function bind(character: Model)
	local humanoid = character:WaitForChild("Humanoid") :: Humanoid
	local function update()
		local ratio = math.clamp(humanoid.Health / humanoid.MaxHealth, 0, 1)
		TweenService:Create(fill, TWEEN_INFO, { Size = UDim2.fromScale(ratio, 1) }):Play()
		fill.BackgroundColor3 = Color3.fromRGB(220, 60, 60):Lerp(Color3.fromRGB(80, 220, 100), ratio)
	end
	humanoid.HealthChanged:Connect(update)
	update()
end

if player.Character then
	bind(player.Character)
end
player.CharacterAdded:Connect(bind)
