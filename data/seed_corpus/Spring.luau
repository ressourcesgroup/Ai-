--!strict
-- Damped spring for smooth camera and UI motion. `speed` is the angular
-- frequency; `damping` is the damping ratio (1 = critically damped, the
-- default: fastest approach with no overshoot; < 1 overshoots; > 1 is sluggish).
-- Step uses the exact closed-form solution of the spring ODE, so it is stable
-- for any dt (explicit Euler blows up when speed * dt gets large on a lag spike).

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
	local x = self.position - self.target
	local v = self.velocity
	local newX, newV
	if math.abs(zeta - 1) < 1e-4 then
		-- Critically damped: x(t) = (c1 + c2 t) e^(-omega t)
		local decay = math.exp(-omega * dt)
		local c2 = v + omega * x
		newX = (x + c2 * dt) * decay
		newV = (c2 - omega * (x + c2 * dt)) * decay
	elseif zeta < 1 then
		-- Underdamped: decaying oscillation at the damped frequency.
		local a = zeta * omega
		local wd = omega * math.sqrt(1 - zeta * zeta)
		local decay = math.exp(-a * dt)
		local cosT, sinT = math.cos(wd * dt), math.sin(wd * dt)
		local b = (v + a * x) / wd
		newX = decay * (x * cosT + b * sinT)
		newV = decay * (v * cosT - (a * b + wd * x) * sinT)
	else
		-- Overdamped: sum of two decaying exponentials.
		local root = math.sqrt(zeta * zeta - 1)
		local r1 = -omega * (zeta - root)
		local r2 = -omega * (zeta + root)
		local c2 = (v - r1 * x) / (r2 - r1)
		local c1 = x - c2
		local e1, e2 = math.exp(r1 * dt), math.exp(r2 * dt)
		newX = c1 * e1 + c2 * e2
		newV = c1 * r1 * e1 + c2 * r2 * e2
	end
	self.position = self.target + newX
	self.velocity = newV
	return self.position
end

function Spring.IsResting(self: Spring, epsilon: number?): boolean
	local e = epsilon or 1e-3
	return math.abs(self.position - self.target) < e and math.abs(self.velocity) < e
end

return Spring
