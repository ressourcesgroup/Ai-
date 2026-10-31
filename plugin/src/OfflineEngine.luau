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

function OfflineEngine.chat(request: { message: string, autonomy: number }): { [string]: any }
	local entry = OfflineEngine.match(request.message)
	if entry == nil then
		return {
			reply_markdown = "I couldn't match that to an offline tool.\n\n" .. capabilities(),
			source = "offline:none",
			actions = {},
			verification = {},
			warnings = {},
		}
	end
	local ok, decoded = pcall(function()
		return HttpService:JSONDecode(entry.json)
	end)
	if not ok or typeof(decoded) ~= "table" then
		return {
			reply_markdown = "Internal error: offline data for '" .. entry.key .. "' could not be decoded.",
			source = "offline:error",
			actions = {},
			verification = {},
			warnings = {},
		}
	end
	local response = decoded :: { [string]: any }
	if request.autonomy <= 1 then
		-- Assistant level: explain only, never propose changes.
		response.actions = {}
		response.reply_markdown = tostring(response.reply_markdown)
			.. "\n\n_Autonomy level 1 (Assistant): no changes proposed. Raise the level to get Apply buttons._"
	end
	return response
end

return OfflineEngine
