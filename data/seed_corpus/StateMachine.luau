--!strict
-- Finite state machine with enter/exit hooks and guarded transitions.

export type State = {
	enter: ((from: string?) -> ())?,
	exit: ((to: string) -> ())?,
	update: ((dt: number) -> ())?,
}

local StateMachine = {}
StateMachine.__index = StateMachine

export type StateMachine = typeof(setmetatable({} :: {
	states: { [string]: State },
	transitions: { [string]: { [string]: boolean } },
	current: string?,
}, StateMachine))

function StateMachine.new(states: { [string]: State }): StateMachine
	return setmetatable({ states = states, transitions = {}, current = nil }, StateMachine)
end

function StateMachine.Allow(self: StateMachine, from: string, to: string)
	self.transitions[from] = self.transitions[from] or {}
	self.transitions[from][to] = true
end

function StateMachine.Start(self: StateMachine, initial: string)
	assert(self.states[initial], `unknown state {initial}`)
	self.current = initial
	local enter = self.states[initial].enter
	if enter then
		enter(nil)
	end
end

function StateMachine.Transition(self: StateMachine, to: string): boolean
	local from = self.current
	if from == nil or not self.states[to] then
		return false
	end
	local allowed = self.transitions[from]
	if not allowed or not allowed[to] then
		return false
	end
	local exit = self.states[from].exit
	if exit then
		exit(to)
	end
	self.current = to
	local enter = self.states[to].enter
	if enter then
		enter(from)
	end
	return true
end

function StateMachine.Update(self: StateMachine, dt: number)
	local state = self.current and self.states[self.current]
	if state and state.update then
		state.update(dt)
	end
end

return StateMachine
