--!strict
-- Markdown: a deliberately small markdown subset for chat replies.
--   * fenced code blocks (```lang ... ```) become separate "code" segments
--   * "# heading" lines become separate "heading" segments (level 1-3)
--   * inside prose: **bold**, *italic*, _italic line_, `inline code`,
--     "- " / "* " bullets and "1. " numbered lists
-- Everything else is shown as plain text. Prose is escaped before rich-text tags
-- are added, so server text can never inject arbitrary rich-text markup.

local Markdown = {}

export type Segment = {
	kind: "text" | "code" | "heading",
	text: string,
	language: string?,
	level: number?,
}

-- Escape the five characters that RichText treats as markup.
function Markdown.escape(text: string): string
	local escaped = text:gsub("&", "&amp;")
	escaped = escaped:gsub("<", "&lt;")
	escaped = escaped:gsub(">", "&gt;")
	escaped = escaped:gsub('"', "&quot;")
	escaped = escaped:gsub("'", "&apos;")
	return escaped
end

-- Split markdown into prose, heading and fenced-code segments, preserving order.
function Markdown.parse(source: string): { Segment }
	local segments: { Segment } = {}
	local prose: { string } = {}
	local code: { string } = {}
	local inCode = false
	local language: string? = nil

	local function flushProse()
		if #prose > 0 then
			local text = table.concat(prose, "\n")
			-- Trim blank lines at the ends; keep inner blank lines as paragraph breaks.
			text = text:gsub("^%s*\n", ""):gsub("\n%s*$", "")
			if text:match("%S") then
				table.insert(segments, { kind = "text", text = text })
			end
			table.clear(prose)
		end
	end

	-- Normalise line endings, then walk line by line.
	local normalised = source:gsub("\r\n", "\n")
	for line in (normalised .. "\n"):gmatch("([^\n]*)\n") do
		local fence = line:match("^%s*```%s*([%w_%+%-]*)%s*$")
		if fence ~= nil then
			if inCode then
				table.insert(segments, { kind = "code", text = table.concat(code, "\n"), language = language })
				table.clear(code)
				inCode = false
				language = nil
			else
				flushProse()
				inCode = true
				language = if fence ~= "" then string.lower(fence) else nil
			end
		elseif inCode then
			table.insert(code, line)
		else
			local hashes, heading = line:match("^%s*(#+)%s+(.-)%s*#*%s*$")
			if hashes and heading and heading ~= "" then
				flushProse()
				table.insert(segments, { kind = "heading", text = heading, level = math.min(#hashes, 3) })
			else
				table.insert(prose, line)
			end
		end
	end

	if inCode then
		-- Unterminated fence: still show the code rather than dropping it.
		table.insert(segments, { kind = "code", text = table.concat(code, "\n"), language = language })
	end
	flushProse()
	return segments
end

local PLACEHOLDER_OPEN = "\1"
local PLACEHOLDER_CLOSE = "\2"

-- Inline formatting of one already-split line. `codeHex` colours inline code.
local function inline(line: string, codeHex: string): string
	-- 1. Pull inline code out first so its contents are never formatted.
	local spans: { string } = {}
	local protected = line:gsub("`([^`]+)`", function(inner: string)
		table.insert(spans, inner)
		return PLACEHOLDER_OPEN .. tostring(#spans) .. PLACEHOLDER_CLOSE
	end)
	-- 2. Escape, then add tags.
	local escaped = Markdown.escape(protected)
	escaped = escaped:gsub("%*%*(.-)%*%*", "<b>%1</b>")
	escaped = escaped:gsub("__(.-)__", "<b>%1</b>")
	escaped = escaped:gsub("%*([^%*%s][^%*]-)%*", "<i>%1</i>")
	escaped = escaped:gsub("^_([^_].-)_$", "<i>%1</i>")
	-- 3. Put inline code back, escaped, in the monospace face.
	escaped = escaped:gsub(PLACEHOLDER_OPEN .. "(%d+)" .. PLACEHOLDER_CLOSE, function(index: string)
		local span = spans[tonumber(index) :: number] or ""
		return '<font face="RobotoMono" color="' .. codeHex .. '">' .. Markdown.escape(span) .. "</font>"
	end)
	return escaped
end

-- Convert a prose segment into RichText markup (escaped first, then tags added).
function Markdown.toRichText(text: string, codeHex: string?): string
	local hex = codeHex or "#C8783C"
	local lines: { string } = {}
	for line in (text .. "\n"):gmatch("([^\n]*)\n") do
		-- Strip the two control characters used as placeholders, should they appear.
		local clean = line:gsub("[\1\2]", "")
		local indent, bullet, rest = clean:match("^(%s*)([%-%*%+])%s+(.*)$")
		local numIndent, number, numRest = clean:match("^(%s*)(%d+)[%.%)]%s+(.*)$")
		local heading = clean:match("^%s*#+%s+(.*)$")
		local rendered: string
		if bullet and rest then
			rendered = string.rep("  ", math.floor(#(indent :: string) / 2)) .. "  •  " .. inline(rest, hex)
		elseif number and numRest then
			rendered = string.rep("  ", math.floor(#(numIndent :: string) / 2)) .. "  " .. number .. ".  " .. inline(numRest, hex)
		elseif heading then
			rendered = "<b>" .. inline(heading, hex) .. "</b>"
		else
			rendered = inline(clean, hex)
		end
		table.insert(lines, rendered)
	end
	return table.concat(lines, "\n")
end

return Markdown
