from pathlib import Path

import pytest

from godmode.luau import LuauSyntaxError, check_structure, lint, tokenize

CORPUS = sorted(Path("data/seed_corpus").glob("*.luau"))


def rules(src):
    return [f.rule for f in lint(src)]


def test_lexer_handles_luau_literals():
    src = 'local s = [==[a]]b]==] .. `x {y + 1} z` .. "q\\"" -- c\nlocal n = 0x_FF + 1e-3 + 1_000'
    kinds = [t.kind for t in tokenize(src)]
    assert kinds.count("string") == 3
    assert kinds.count("number") == 3


def test_lexer_rejects_non_luau_operator():
    with pytest.raises(LuauSyntaxError):
        tokenize("if a != b then end")


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.name)
def test_seed_corpus_is_structurally_valid_and_lint_clean(path):
    src = path.read_text()
    result = check_structure(src)
    assert result.ok, result.errors
    assert lint(src) == []


@pytest.mark.parametrize(
    "src",
    [
        "local x = if a then b else c",
        "local x = f(if a then 1 elseif b then 2 else 3)",
        "local function f(): Part?\n\tif x then\n\t\treturn nil\n\tend\nend",
        "local function f(): Map<K, V>\n\tif x then\n\tend\nend",
        "for i = 1, 10 do\n\twhile true do\n\t\tbreak\n\tend\nend",
        "repeat\n\tx += 1\nuntil x > 3",
        "return if a then function() end else nil",
        # Nested if-expressions (else-if / then-if chains).
        "local x = if a then 1 else if b then 2 else 3",
        "local x = if a then if b then 1 else 2 else 3",
        "local x = if a then b elseif c then d else if e then f else g",
        "local f = if a then function() if x then y() end end else nil",
        # A statement-level `if` right after a return type on the same line.
        "local function f(): number? if x then return 1 end return nil end",
        "local function f(): Map<K, V> if x then end end",
        "function M.Get<T>(self: M, key: string): T? if key then end return nil end",
        "local function f(cb: (number) -> ()?): ((number) -> ()) | nil if cb then end return nil end",
        "local x: number? = nil if x then end",
        "type T = number? if x then end",
        "local v = if a > b then a else b",
        "while if a then b else c do end",
        # Strings: \z continuation, braces/backticks inside interpolations, escapes.
        'local s = "a\\z\n   b"',
        'local s = `{t["{"]}`',
        "local s = `a \\{ \\` {x}`",
        "local s = `x {`in {y}`} z`",
        # Assorted valid Luau syntax.
        "type F = (number) -> string",
        "export type T<U> = {[string]: U}",
        "type G = <T>(T) -> T",
        "export type Fn = (a: number, b: string) -> boolean",
        "function f<T>(x: T): T return x end",
        'local s = ("x"):rep(3)',
        "local s = ''\ns ..= 'a'\nlocal n = 7\nn //= 2",
        "for i = 1, 3 do\n\tif i == 2 then continue end\nend",
        "@native function f() end",
        "@checked function f(x: number): number return x end",
        "local s = [==[ a ]] ]==] --[=[ c ]] ]=]",
        "local n = 0b1010 + 1_000_000 + .5 + 0xFF",
        "local x = 1\nlocal y: typeof(x) = x",
        'type Mode = "a" | "b"\nlocal m: Mode = "a"',
        "local t = { a = if x then 1 else 2, b = 3 }",
        'local p = Instance.new("Part")\n\t:Clone()\n\t:Destroy()',
        "local function f(...: number): ...number return ... end",
        "for k: string, v: number in pairs(t) do if v then end end",
    ],
)
def test_valid_programs_pass(src):
    assert check_structure(src).ok, check_structure(src).errors


@pytest.mark.parametrize(
    "src",
    [
        "local function f()\n\treturn 1\n",
        "if x then\nend\nend",
        "local t = { 1, 2",
        "repeat\n\tx += 1\n",
        "for i = 1, 3\n\tprint(i)\nend",
        'local s = "unterminated',
        "else\nend",
        "local x = if a then 1 else if b then 2",
        "local x = if a then 1",
        "local function f(): number? if x then return 1 end",
        'local s = "a\nb"',
        "local s = `a\nb`",
        "local s = `{x`",
        "local s = `a{{b}}`",
    ],
)
def test_broken_programs_fail(src):
    assert not check_structure(src).ok


def test_lint_unvalidated_remote_handler():
    bad = "remote.OnServerEvent:Connect(function(player, amount)\n\tcoins.Value += amount\nend)"
    good = 'remote.OnServerEvent:Connect(function(player, amount)\n\tif typeof(amount) ~= "number" then return end\nend)'
    assert "GM002" in rules(bad)
    assert "GM002" not in rules(good)


def test_lint_handler_without_client_args_is_fine():
    assert rules("remote.OnServerEvent:Connect(function(player)\n\tprint(player)\nend)") == []


def test_lint_invoke_handler_assignment():
    src = "rf.OnServerInvoke = function(player, item)\n\treturn shop[item]\nend"
    assert "GM002" in rules(src)


def test_lint_loadstring_and_deprecated():
    assert "GM001" in rules("loadstring(src)()")
    assert "GM006" in rules("wait(1)")
    assert "GM006" not in rules("task.wait(1)")


def test_lint_datastore_pcall_and_wrappers():
    assert "GM007" in rules("local v = store:GetAsync(key)")
    assert "GM007" not in rules("local ok, v = pcall(function() return store:GetAsync(key) end)")
    wrapped = (
        "local function retry(fn)\n\treturn pcall(fn)\nend\n"
        "retry(function() return store:SetAsync(k, v) end)"
    )
    assert "GM007" not in rules(wrapped)


GM002_FLAGGED = [
    # Named handler defined in the same file (local function and local = function).
    "local function onEmote(player, emote)\n\tprint(emote)\nend\nremote.OnServerEvent:Connect(onEmote)",
    "local onEmote = function(player, emote)\n\tprint(emote)\nend\nremote.OnServerEvent:Connect(onEmote)",
    "function remote.OnServerInvoke(p, x)\n\treturn shop[x]\nend",
    "remote.OnServerEvent:Connect(function(player, ...)\n\tdoThing(...)\nend)",
    "remote.OnServerEvent:Connect(function(player, ...)\n\tlocal a = ...\n\tcoins.Value += a\nend)",
    # A validator name elsewhere in the body does not validate the argument.
    "remote.OnServerEvent:Connect(function(player, amount)\n\tprint(tostring(amount))\n\tcoins.Value += amount\nend)",
    "remote.OnServerEvent:Connect(function(player, a, b)\n\tif typeof(a) ~= 'number' then return end\n\tcoins.Value += a + b\nend)",
    "remote.OnServerEvent:Connect(function(player, item)\n\tlocal price = PRICES[item]\n\tcoins.Value -= price\nend)",
]

GM002_CLEAN = [
    "local function onEmote(player, emote)\n\tif typeof(emote) ~= 'string' then return end\nend\nremote.OnServerEvent:Connect(onEmote)",
    "function remote.OnServerInvoke(p, x)\n\tif type(x) ~= 'string' then return end\n\treturn shop[x]\nend",
    "remote.OnServerEvent:Connect(function(player, ...)\n\tlocal a = ...\n\tif typeof(a) ~= 'number' then return end\nend)",
    "remote.OnServerEvent:Connect(function(player, ...)\n\tif select('#', ...) ~= 1 then return end\nend)",
    "remote.OnServerEvent:Connect(function(player, _a, unused)\n\tprint(player)\nend)",
    "remote.OnServerEvent:Connect(function(player, item)\n\tif not ALLOWED[item] then return end\nend)",
    "remote.OnServerEvent:Connect(function(player, item)\n\tlocal price = PRICES[item]\n\tif price == nil then return end\nend)",
    "remote.OnServerEvent:Connect(function(player, part)\n\tif not part:IsA('BasePart') then return end\nend)",
    "remote.OnServerEvent:Connect(function(player, n)\n\tlocal v = tonumber(n)\nend)",
    "local function valid(x) return typeof(x) == 'number' end\n"
    "remote.OnServerEvent:Connect(function(player, n)\n\tif not valid(n) then return end\nend)",
]


@pytest.mark.parametrize("src", GM002_FLAGGED)
def test_gm002_flags_unvalidated_handlers(src):
    assert "GM002" in rules(src)


@pytest.mark.parametrize("src", GM002_CLEAN)
def test_gm002_accepts_validated_handlers(src):
    assert "GM002" not in rules(src)


def test_gm002_names_the_unvalidated_parameter():
    src = "remote.OnServerEvent:Connect(function(player, a, b)\n\tif typeof(a) ~= 'number' then return end\n\tx = a + b\nend)"
    (finding,) = lint(src)
    assert "'b'" in finding.message and "'a'" not in finding.message


def test_gm007_scoping():
    long_pcall = "local ok, v = pcall(function()\n" + "\tlocal a = 1\n" * 6 + "\treturn store:GetAsync(key)\nend)"
    assert "GM007" not in rules(long_pcall)
    assert "GM007" in rules("local x = pcall(print)\nlocal v = store:GetAsync(key)")
    assert "GM007" in rules("local function f()\n\tpcall(print)\nend\nlocal v = store:GetAsync(key)")
    protector_body = "local function load(k)\n\tlocal ok, v = pcall(fn)\n\treturn store:GetAsync(k)\nend"
    assert "GM007" not in rules(protector_body)


def test_gm006_skips_locally_bound_names():
    assert "GM006" not in rules("local function wait(t) end\nwait(1)")
    assert "GM006" not in rules("local wait = require(x)\nwait(1)")
    assert "GM006" in rules("spawn(f)")


def test_gm001_global_loadstring_and_environment_indexing():
    assert "GM001" not in rules("obj:loadstring(x)\nobj.loadstring(x)")
    assert "GM001" in rules('getfenv()["load".."string"](x)')
    assert "GM001" in rules("getfenv().loadstring(x)")
    assert "GM001" in rules("local f = loadstring\nf(x)()")


def test_lexer_line_numbers_after_multiline_strings():
    toks = tokenize('local s = "a\\z\n  b"\nlocal t = [[\n]]\nx')
    assert toks[-2].value == "x" and toks[-2].line == 5


# String escapes, mirroring Luau's Lexer::fixupQuotedString (Ast/src/Lexer.cpp).
# Regression: JSON-style "\u00e1" in the generated OfflineData.luau is a malformed
# escape in Luau, which made that module (and so the whole plugin) fail to load.
@pytest.mark.parametrize(
    "literal",
    [r'"\u00e1"', r'"\u{}"', r'"\u{110000}"', r'"\u{e1"', r'"\x4"', r'"\xZZ"', r'"\256"', r'"\999"', '"a\\\0b"'],
)
def test_lexer_rejects_malformed_escapes(literal):
    with pytest.raises(LuauSyntaxError, match="malformed escape"):
        tokenize(f"local s = {literal}")
    assert not check_structure(f"local s = {literal}\n").ok


@pytest.mark.parametrize(
    "literal",
    [
        r'"\u{e1}"', r'"\u{10FFFF}"', r'"\x41\x7e"', r'"\255\0\65"', r'"\a\b\f\n\r\t\v\\\"\'"',
        r'"a\z   b"', '"a\\\nb"', '"a\\\r\nb"',
        # Luau keeps unknown escapes as the character itself (unescape() default branch),
        # e.g. a Windows path in the external training corpus: "C:\RC7\rc7.dat".
        r'"\q"', r'"C:\RC7\rc7.dat"', r'"\{"', r'"\`"', r"'é ñ ü'",
    ],
)
def test_lexer_accepts_valid_escapes(literal):
    assert [t.kind for t in tokenize(f"local s = {literal}")].count("string") == 1


def test_interpolated_string_escapes():
    assert check_structure("local s = `a \\{b\\} \\` {c}`\n").ok
    with pytest.raises(LuauSyntaxError, match="malformed escape"):
        tokenize("local s = `\\u00e1 {c}`")
