--!strict
-- Maid: tracks cleanup tasks so systems never leak connections or instances.
-- Tasks are cleaned in reverse order; each runs in pcall so a failure is
-- reported without skipping the remaining tasks.

type Task = RBXScriptConnection | Instance | thread | () -> () | { Destroy: (any) -> () }

local Maid = {}
Maid.__index = Maid

export type Maid = typeof(setmetatable({} :: { _tasks: { Task } }, Maid))

function Maid.new(): Maid
	return setmetatable({ _tasks = {} }, Maid)
end

function Maid.Give<T>(self: Maid, item: T & Task): T
	table.insert(self._tasks, item)
	return item
end

local function cleanup(item: Task)
	if typeof(item) == "RBXScriptConnection" then
		item:Disconnect()
	elseif typeof(item) == "Instance" then
		item:Destroy()
	elseif typeof(item) == "thread" then
		-- Cancelling the running thread (a task that cleans its own maid)
		-- would abort the rest of the cleanup, so skip it.
		if item ~= coroutine.running() and coroutine.status(item) ~= "dead" then
			task.cancel(item)
		end
	elseif typeof(item) == "function" then
		item()
	elseif typeof(item) == "table" then
		item:Destroy()
	end
end

function Maid.Clean(self: Maid)
	local tasks = self._tasks
	self._tasks = {}
	for index = #tasks, 1, -1 do
		-- One failing task must not stop the others from being cleaned up.
		local ok, err = pcall(cleanup, tasks[index])
		if not ok then
			warn(`Maid task {index} failed to clean up: {err}`)
		end
	end
end

Maid.Destroy = Maid.Clean

return Maid
