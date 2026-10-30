--!strict
-- HttpClient: JSON-over-HTTP calls to the local GODMODE server.
-- All network access goes through HttpService:RequestAsync inside pcall, with a
-- client-side timeout, and every failure is turned into a human-readable message.

local HttpService = game:GetService("HttpService")

local HttpClient = {}
HttpClient.__index = HttpClient

export type Response = {
	ok: boolean,
	status: number,
	body: any,
	error: string?,
}

export type Client = typeof(setmetatable(
	{} :: { baseUrl: string, token: string?, timeout: number },
	HttpClient
))

local DEFAULT_TIMEOUT = 120

local PERMISSION_HELP = "Studio blocked the HTTP request. (1) When Studio asks whether GODMODE may "
	.. "send HTTP requests to 127.0.0.1, choose Allow; if you dismissed that prompt, open "
	.. "Plugins > Manage Plugins, find GODMODE and allow its HTTP access. (2) Make sure "
	.. "Game Settings > Security > Allow HTTP Requests is on for this place."

function HttpClient.new(baseUrl: string, token: string?, timeout: number?): Client
	local self = setmetatable({
		baseUrl = HttpClient.normaliseUrl(baseUrl),
		token = if token ~= nil and token ~= "" then token else nil,
		timeout = timeout or DEFAULT_TIMEOUT,
	}, HttpClient)
	return self
end

-- Strip whitespace and trailing slashes; default to http:// when no scheme is given.
function HttpClient.normaliseUrl(url: string): string
	local trimmed = url:match("^%s*(.-)%s*$") or url
	trimmed = trimmed:gsub("/+$", "")
	if not trimmed:match("^https?://") then
		trimmed = "http://" .. trimmed
	end
	return trimmed
end

-- Translate a raw RequestAsync error into a helpful message.
function HttpClient.explainError(message: string): string
	local lower = message:lower()
	if lower:find("not enabled", 1, true)
		or lower:find("permission", 1, true)
		or lower:find("not allowed", 1, true)
		or lower:find("denied", 1, true)
	then
		return PERMISSION_HELP .. " (Studio said: " .. message .. ")"
	end
	if lower:find("connectfail", 1, true)
		or lower:find("couldn't connect", 1, true)
		or lower:find("connection refused", 1, true)
		or lower:find("httperror", 1, true)
	then
		return "Could not reach the GODMODE server. Is it running? Start it with "
			.. "`python -m godmode.server` and check the server URL in settings. (" .. message .. ")"
	end
	return message
end

-- Run RequestAsync on its own thread and race it against a timeout.
local function requestWithTimeout(options: { [string]: any }, timeout: number): (boolean, any)
	local finished = false
	local success = false
	local payload: any = nil
	local signal = Instance.new("BindableEvent")

	task.spawn(function()
		local ok, result = pcall(function()
			return HttpService:RequestAsync(options :: any)
		end)
		if not finished then
			finished = true
			success = ok
			payload = result
			signal:Fire()
		end
	end)

	task.delay(timeout, function()
		if not finished then
			finished = true
			success = false
			payload = "request timed out after " .. tostring(timeout) .. " seconds"
			signal:Fire()
		end
	end)

	if not finished then
		signal.Event:Wait()
	end
	signal:Destroy()
	return success, payload
end

function HttpClient.request(self: Client, method: string, path: string, body: any?): Response
	local headers: { [string]: string } = {
		["Accept"] = "application/json",
	}
	if self.token then
		-- docs/PLUGIN_PROTOCOL.md: optional shared secret header.
		headers["X-Godmode-Token"] = self.token
	end

	local options: { [string]: any } = {
		Url = self.baseUrl .. path,
		Method = method,
		Headers = headers,
	}

	if body ~= nil then
		local encodedOk, encoded = pcall(function()
			return HttpService:JSONEncode(body)
		end)
		if not encodedOk then
			return { ok = false, status = 0, body = nil, error = "could not encode request: " .. tostring(encoded) }
		end
		headers["Content-Type"] = "application/json"
		options.Body = encoded
	end

	local ok, result = requestWithTimeout(options, self.timeout)
	if not ok then
		return { ok = false, status = 0, body = nil, error = HttpClient.explainError(tostring(result)) }
	end

	local decoded: any = nil
	local rawBody = result.Body
	if typeof(rawBody) == "string" and rawBody ~= "" then
		local decodeOk, value = pcall(function()
			return HttpService:JSONDecode(rawBody)
		end)
		if decodeOk then
			decoded = value
		end
	end

	if not result.Success then
		local detail = ""
		-- Protocol error shape: {"ok": false, "error": {"code": "...", "message": "..."}}
		if typeof(decoded) == "table" and typeof(decoded.error) == "table" then
			local code = decoded.error.code
			local text = decoded.error.message
			detail = ": " .. (if typeof(code) == "string" then code .. " - " else "")
				.. (if typeof(text) == "string" then text else "")
		elseif typeof(decoded) == "table" and typeof(decoded.error) == "string" then
			detail = ": " .. decoded.error
		elseif typeof(rawBody) == "string" then
			detail = ": " .. rawBody:sub(1, 300)
		end
		local message = "server returned HTTP " .. tostring(result.StatusCode) .. detail
		if result.StatusCode == 401 or result.StatusCode == 403 then
			message ..= " (check the access token in GODMODE Settings; it must match the server's --token)"
		end
		return { ok = false, status = result.StatusCode, body = decoded, error = message }
	end

	if typeof(decoded) ~= "table" then
		return { ok = false, status = result.StatusCode, body = nil, error = "server response was not a JSON object" }
	end

	if typeof(decoded) == "table" and decoded.ok == false then
		local message = "server reported an error"
		if typeof(decoded.error) == "table" and typeof(decoded.error.message) == "string" then
			message ..= ": " .. decoded.error.message
		end
		return { ok = false, status = result.StatusCode, body = decoded, error = message }
	end

	return { ok = true, status = result.StatusCode, body = decoded, error = nil }
end

function HttpClient.get(self: Client, path: string): Response
	return self:request("GET", path, nil)
end

function HttpClient.post(self: Client, path: string, body: any): Response
	return self:request("POST", path, body)
end

return HttpClient
