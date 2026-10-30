--!strict
-- Bezier curve utilities for projectiles, cameras and procedural paths.

local Bezier = {}

function Bezier.quadratic(p0: Vector3, p1: Vector3, p2: Vector3, t: number): Vector3
	local u = 1 - t
	return p0 * (u * u) + p1 * (2 * u * t) + p2 * (t * t)
end

function Bezier.cubic(p0: Vector3, p1: Vector3, p2: Vector3, p3: Vector3, t: number): Vector3
	local u = 1 - t
	return p0 * (u * u * u) + p1 * (3 * u * u * t) + p2 * (3 * u * t * t) + p3 * (t * t * t)
end

-- Approximates arc length by sampling the curve.
function Bezier.length(points: { Vector3 }, samples: number?): number
	local count = samples or 32
	local total = 0
	local previous = points[1]
	for i = 1, count do
		local t = i / count
		local current = if #points == 3
			then Bezier.quadratic(points[1], points[2], points[3], t)
			else Bezier.cubic(points[1], points[2], points[3], points[4], t)
		total += (current - previous).Magnitude
		previous = current
	end
	return total
end

return Bezier
