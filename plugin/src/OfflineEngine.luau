--!strict
-- Offline engine: answers /chat requests inside Studio with no server.
--
-- It matches the request to one of the embedded, pre-verified tool responses
-- (OfflineData) by keyword and returns it in the same shape as the server's
-- /chat reply, so ActionPreview / ActionApplier work unchanged. It is a
-- keyword matcher over fixed outputs, not a neural model, and says so.

local HttpService = game:GetService("HttpService")

local OfflineData = require(script.Parent.OfflineData)

local OfflineEngine = {}

local ANIMATION_WORDS = { "animation", "animate", "anim", "emote", "keyframe" }

local function countMatches(text: string, words: { string }): number
	local n = 0
	for _, word in words do
		if string.find(text, word, 1, true) then
			n += 1
		end
	end
	return n
end

local function capabilities(): string
	local tools, anims = {}, {}
	for _, entry in OfflineData do
		if entry.animation then
			table.insert(anims, (string.gsub(entry.key, "^anim_", "")))
		else
			table.insert(tools, entry.key)
		end
	end
	return "**Offline mode** (no server needed) can build: " .. table.concat(tools, ", ")
		.. ".\n\nAnimations (R15): " .. table.concat(anims, ", ")
		.. ".\n\nTry: *build an obby*, *make a sprint animation*, *save player data*, *make an aw shucks emote*."
		.. "\n\nQuestions, code search and GODMODE's own neural model need the optional local server "
		.. "(type `/server on` after starting `python -m godmode.server`)."
end

export type Ranked = { entry: OfflineData.Entry, score: number }

-- Score every entry the same way `match` does (keywords x2, +1 when the
-- animation intent agrees), highest first; ties keep OfflineData order.
function OfflineEngine.rank(message: string): ({ Ranked }, boolean)
	local text = string.lower(message)
	local wantsAnimation = countMatches(text, ANIMATION_WORDS) > 0
	local ranked: { Ranked } = {}
	for index, entry in OfflineData do
		local score = countMatches(text, entry.keywords) * 2
		if score > 0 and entry.animation == wantsAnimation then
			score += 1
		end
		if score > 0 then
			table.insert(ranked, { entry = entry, score = score, index = index } :: any)
		end
	end
	table.sort(ranked, function(a: any, b: any): boolean
		if a.score ~= b.score then
			return a.score > b.score
		end
		return a.index < b.index
	end)
	return ranked, wantsAnimation
end

function OfflineEngine.match(message: string): OfflineData.Entry?
	local text = string.lower(message)
	local wantsAnimation = countMatches(text, ANIMATION_WORDS) > 0
	local best: OfflineData.Entry? = nil
	local bestScore = 0
	for _, entry in OfflineData do
		local score = countMatches(text, entry.keywords) * 2
		if score > 0 and entry.animation == wantsAnimation then
			score += 1
		end
		if score > bestScore then
			best, bestScore = entry, score
		end
	end
	return best
end

export type ThinkingStep = { label: string, detail: string, ms: number }

-- An honest trace of what the offline matcher actually did. The requested
-- thinking level is echoed back but changes nothing offline.
local function newTrace(level: string?)
	local started = os.clock()
	local last = started
	local steps: { ThinkingStep } = {}
	local function step(label: string, detail: string)
		local now = os.clock()
		table.insert(steps, { label = label, detail = detail, ms = math.floor((now - last) * 1000 + 0.5) })
		last = now
	end
	local function finish(): { [string]: any }
		return {
			level = level or "medium",
			summary = "Offline: thinking level has no effect without the server",
			elapsed_ms = math.floor((os.clock() - started) * 1000 + 0.5),
			steps = steps,
		}
	end
	return step, finish
end

local function topScores(ranked: { Ranked }): string
	if #ranked == 0 then
		return "no keyword matched"
	end
	local parts: { string } = {}
	for i = 1, math.min(3, #ranked) do
		table.insert(parts, ranked[i].entry.key .. "=" .. tostring(ranked[i].score))
	end
	return table.concat(parts, ", ")
end

function OfflineEngine.chat(request: { message: string, autonomy: number, thinking: string? }): { [string]: any }
	local step, finish = newTrace(request.thinking)
	step("Read request", tostring(#request.message) .. " characters")
	local ranked, wantsAnimation = OfflineEngine.rank(request.message)
	step("Detected intent", if wantsAnimation then "animation" else "build / script")
	step("Matched keywords", "top scores: " .. topScores(ranked))
	local entry = OfflineEngine.match(request.message)
	if entry == nil then
		step("No offline build matched", "listing what offline mode can build")
		return {
			reply_markdown = "I couldn't match that to an offline tool.\n\n" .. capabilities(),
			source = "offline:none",
			actions = {},
			verification = {},
			warnings = {},
			thinking = finish(),
		}
	end
	step("Picked offline build " .. entry.key, "highest keyword score")
	local ok, decoded = pcall(function()
		return HttpService:JSONDecode(entry.json)
	end)
	if not ok or typeof(decoded) ~= "table" then
		step("Decode failed", "embedded data for " .. entry.key .. " is invalid")
		return {
			reply_markdown = "Internal error: offline data for '" .. entry.key .. "' could not be decoded.",
			source = "offline:error",
			actions = {},
			verification = {},
			warnings = {},
			thinking = finish(),
		}
	end
	local response = decoded :: { [string]: any }
	local passed, total = 0, 0
	if typeof(response.verification) == "table" then
		for _, check in ipairs(response.verification) do
			if typeof(check) == "table" then
				total += 1
				if check.ok == true then
					passed += 1
				end
			end
		end
	end
	step(
		"Loaded pre-verified result (" .. tostring(passed) .. " checks passed)",
		tostring(passed) .. "/" .. tostring(total) .. " checks passed when the plugin was built"
	)
	if request.autonomy <= 1 then
		-- Assistant level: explain only, never propose changes.
		response.actions = {}
		response.reply_markdown = tostring(response.reply_markdown)
			.. "\n\n_Autonomy level 1 (Assistant): no changes proposed. Raise the level to get Apply buttons._"
		step("Applied autonomy 1", "removed proposed actions")
	end
	response.thinking = finish()
	return response
end

return OfflineEngine
