--!strict
-- Lightweight signal implementation with typed connections.
-- Fire snapshots the handler list first, so handlers that connect or
-- disconnect during a fire do not change who receives the current event.

export type Connection = {
	Connected: boolean,
	Disconnect: (self: Connection) -> (),
}

type Handler<T...> = (T...) -> ()

local Signal = {}
Signal.__index = Signal

export type Signal<T...> = typeof(setmetatable({} :: { _handlers: { [Connection]: Handler<T...> } }, Signal))

function Signal.new<T...>(): Signal<T...>
	local handlers: { [Connection]: Handler<T...> } = {}
	return setmetatable({ _handlers = handlers }, Signal)
end

function Signal.Connect<T...>(self: Signal<T...>, handler: Handler<T...>): Connection
	local connection: Connection
	connection = {
		Connected = true,
		Disconnect = function(conn: Connection)
			if not conn.Connected then
				return
			end
			conn.Connected = false
			self._handlers[conn] = nil
		end,
	}
	self._handlers[connection] = handler
	return connection
end

function Signal.Fire<T...>(self: Signal<T...>, ...: T...)
	local snapshot: { { connection: Connection, handler: Handler<T...> } } = {}
	for connection, handler in self._handlers do
		table.insert(snapshot, { connection = connection, handler = handler })
	end
	for _, entry in snapshot do
		-- Skip handlers disconnected by an earlier handler in this same fire.
		if entry.connection.Connected then
			task.spawn(entry.handler, ...)
		end
	end
end

function Signal.Once<T...>(self: Signal<T...>, handler: Handler<T...>): Connection
	local connection: Connection
	connection = self:Connect(function(...: T...)
		connection:Disconnect()
		handler(...)
	end)
	return connection
end

function Signal.DisconnectAll<T...>(self: Signal<T...>)
	for connection in self._handlers do
		connection.Connected = false
	end
	table.clear(self._handlers)
end

return Signal
