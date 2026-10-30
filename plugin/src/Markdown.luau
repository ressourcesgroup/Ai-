--!strict
-- Markdown: a deliberately small markdown subset for chat bubbles.
--   * fenced code blocks (```lang ... ```) become separate monospace segments
--   * inside prose: **bold**, `inline code`, and "# heading" lines
-- Everything else is shown as plain text. Prose is escaped before rich-text tags
-- are added, so server text can never inject arbitrary rich-text markup.

local Markdown = {}

export type Segment = {
	kind: "text" | "code",
	text: string,
	language: string?,
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

-- Split markdown into prose and fenced-code segments, preserving order.
function Markdown.parse(source: string): { Segment }
	local segments: { Segment } = {}
	local prose: { string } = {}
	local code: { string } = {}
	local inCode = false
	local language: string? = nil

	local function flushProse()
		if #prose > 0 then
			local text = table.concat(prose, "\n")
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
				language = if fence ~= "" then fence else nil
			end
		elseif inCode then
			table.insert(code, line)
		else
			table.insert(prose, line)
		end
	end

	if inCode then
		-- Unterminated fence: still show the code rather than dropping it.
		table.insert(segments, { kind = "code", text = table.concat(code, "\n"), language = language })
	end
	flushProse()
	return segments
end

-- Convert a prose segment into RichText markup (escaped first, then tags added).
function Markdown.toRichText(text: string): string
	local lines: { string } = {}
	for line in (text .. "\n"):gmatch("([^\n]*)\n") do
		local heading = line:match("^%s*#+%s+(.*)$")
		local escaped = Markdown.escape(heading or line)
		escaped = escaped:gsub("%*%*(.-)%*%*", "<b>%1</b>")
		escaped = escaped:gsub("`([^`]+)`", '<font face="RobotoMono">%1</font>')
		escaped = escaped:gsub("^(%s*)[%-%*] ", "%1• ")
		if heading then
			escaped = "<b>" .. escaped .. "</b>"
		end
		table.insert(lines, escaped)
	end
	return table.concat(lines, "\n")
end

return Markdown
