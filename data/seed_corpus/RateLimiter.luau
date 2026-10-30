--!strict
-- Token-bucket rate limiter keyed by player, used to guard RemoteEvents.

local Players = game:GetService("Players")

local RateLimiter = {}
RateLimiter.__index = RateLimiter

type Bucket = { tokens: number, updated: number }

export type RateLimiter = typeof(setmetatable({} :: {
	_capacity: number,
	_refillPerSecond: number,
	_buckets: { [Player]: Bucket },
}, RateLimiter))

function RateLimiter.new(capacity: number, refillPerSecond: number): RateLimiter
	assert(capacity > 0, "capacity must be positive")
	assert(refillPerSecond > 0, "refill rate must be positive")
	local self = setmetatable({
		_capacity = capacity,
		_refillPerSecond = refillPerSecond,
		_buckets = {},
	}, RateLimiter)
	Players.PlayerRemoving:Connect(function(player)
		self._buckets[player] = nil
	end)
	return self
end

function RateLimiter.Allow(self: RateLimiter, player: Player, cost: number?): boolean
	local now = os.clock()
	local price = cost or 1
	local bucket = self._buckets[player]
	if not bucket then
		bucket = { tokens = self._capacity, updated = now }
		self._buckets[player] = bucket
	end
	local elapsed = now - bucket.updated
	bucket.tokens = math.min(self._capacity, bucket.tokens + elapsed * self._refillPerSecond)
	bucket.updated = now
	if bucket.tokens < price then
		return false
	end
	bucket.tokens -= price
	return true
end

return RateLimiter
