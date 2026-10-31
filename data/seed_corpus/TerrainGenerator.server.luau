--!strict
-- Procedural island heightmap using layered Perlin noise and Terrain:FillBlock.
-- Water is filled FIRST so the land columns written afterwards replace it;
-- filling water last would overwrite the sand and grass bases below sea level.

local Terrain = workspace.Terrain

local SIZE = 64
local CELL = 4
local HEIGHT = 40
local OCTAVES = 4
local SEED = 1337
local SEA_LEVEL = 4

local function fractalNoise(x: number, z: number): number
	local amplitude, frequency, total, norm = 1, 1 / 48, 0, 0
	for _ = 1, OCTAVES do
		total += math.noise(x * frequency, z * frequency, SEED) * amplitude
		norm += amplitude
		amplitude *= 0.5
		frequency *= 2
	end
	return total / norm
end

local function islandMask(x: number, z: number): number
	local half = SIZE / 2
	local dx, dz = (x - half) / half, (z - half) / half
	return math.clamp(1 - math.sqrt(dx * dx + dz * dz), 0, 1)
end

local worldSize = SIZE * CELL
Terrain:FillBlock(
	CFrame.new(worldSize / 2, SEA_LEVEL / 2, worldSize / 2),
	Vector3.new(worldSize * 2, SEA_LEVEL, worldSize * 2),
	Enum.Material.Water
)

for x = 0, SIZE - 1 do
	for z = 0, SIZE - 1 do
		local h = (fractalNoise(x, z) * 0.5 + 0.5) * islandMask(x, z) * HEIGHT
		local height = math.max(CELL, math.floor(h / CELL) * CELL)
		local material = if height > HEIGHT * 0.6 then Enum.Material.Rock
			elseif height > CELL * 2 then Enum.Material.Grass
			else Enum.Material.Sand
		local center = Vector3.new(x * CELL, height / 2, z * CELL)
		Terrain:FillBlock(CFrame.new(center), Vector3.new(CELL, height, CELL), material)
	end
	if x % 8 == 0 then
		task.wait()
	end
end

