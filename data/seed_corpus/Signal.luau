--!strict
-- Lightweight signal implementation with typed connections.

export type Connection = {
	Connected: boolean,
	Disconnect: (self: Connection) -> (),
}

local Signal = {}
Signal.__index = Signal

export type Signal<T...> = typeof(setmetatable({} :: { _handlers: { [Connection]: (T...) -> () } }, Signal))

function Signal.new<T...>(): Signal<T...>
	return setmetatable({ _handlers = {} }, Signal) :: any
end

function Signal.Connect<T...>(self: Signal<T...>, handler: (T...) -> ()): Connection
	local connection = { Connected = true } :: any
	function connection.Disconnect(conn: Connection)
		if not conn.Connected then
			return
		end
		conn.Connected = false
		self._handlers[conn] = nil
	end
	self._handlers[connection] = handler
	return connection
end

function Signal.Fire<T...>(self: Signal<T...>, ...: T...)
	for connection, handler in self._handlers do
		if connection.Connected then
			task.spawn(handler, ...)
		end
	end
end

function Signal.Once<T...>(self: Signal<T...>, handler: (T...) -> ()): Connection
	local connection: Connection
	connection = self:Connect(function(...)
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
