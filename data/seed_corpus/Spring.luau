--!strict
-- Critically damped spring for smooth camera and UI motion.

local Spring = {}
Spring.__index = Spring

export type Spring = typeof(setmetatable({} :: {
	position: number,
	velocity: number,
	target: number,
	speed: number,
	damping: number,
}, Spring))

function Spring.new(initial: number, speed: number?, damping: number?): Spring
	return setmetatable({
		position = initial,
		velocity = 0,
		target = initial,
		speed = speed or 10,
		damping = damping or 1,
	}, Spring)
end

function Spring.Step(self: Spring, dt: number): number
	local omega = self.speed
	local zeta = self.damping
	local displacement = self.position - self.target
	local acceleration = -2 * zeta * omega * self.velocity - omega * omega * displacement
	self.velocity += acceleration * dt
	self.position += self.velocity * dt
	return self.position
end

function Spring.IsResting(self: Spring, epsilon: number?): boolean
	local e = epsilon or 1e-3
	return math.abs(self.position - self.target) < e and math.abs(self.velocity) < e
end

return Spring
