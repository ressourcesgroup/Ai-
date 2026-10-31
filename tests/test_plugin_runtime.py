"""Execution tests for the Roblox Studio plugin, in a real Lua 5.4 VM.

The plugin's Luau is translated to Lua 5.4 (``godmode.luau.to_lua``) and run
through the system liblua (``godmode.luau.lua54``). This is a test harness, not
Roblox Studio and not the Luau VM: it proves the plugin's own logic executes,
and (with ``roblox_mock.lua``) calls the Roblox API the way it should against a
hand-written model of that API. Everything here is skipped when liblua 5.4 is
not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from godmode.luau import lua54
from godmode.luau.to_lua import TranslateError, translate

pytestmark = pytest.mark.skipif(not lua54.available(), reason="liblua 5.4 not installed")

SRC = Path(__file__).resolve().parents[1] / "plugin" / "src"
MODULES = sorted(SRC.glob("*.luau"))

# Minimal stand-ins for the helpers the translator emits; roblox_mock.lua has the
# full versions. Enough to execute translated snippets in isolation.
HELPERS = r"""
function __iter(t, ...)
  if type(t) == "function" then return t, ... end
  return next, t, nil
end
function __cat(a, b)
  local function s(v)
    if math.type(v) == "float" and v == math.floor(v) and v > -1e15 and v < 1e15 then
      return string.format("%d", v)
    end
    return tostring(v)
  end
  return s(a) .. s(b)
end
"""


def run_luau(code: str):
    with lua54.LuaState() as L:
        L.run(HELPERS)
        L.set_string("__chunk", translate(code))
        return L.run('local f = assert(load(__chunk, "@snippet.luau")) return f()')


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_every_plugin_module_translates_and_compiles(path):
    src = path.read_text(encoding="utf-8")
    out = translate(src)
    assert out.count("\n") == src.count("\n"), "translation must keep line numbers"
    with lua54.LuaState() as L:
        L.set_string("__chunk", out)
        assert L.run(f'local f, e = load(__chunk, "@{path.name}") return e or "ok"') == "ok"


def test_plugin_has_the_expected_modules():
    names = {p.stem for p in MODULES}
    assert {"Main", "ChatUI", "ActionApplier", "ActionPreview", "OfflineEngine", "OfflineData",
            "PathResolver", "PropertyDecoder", "Markdown", "HttpClient", "Context", "Theme", "Ui"} <= names


@pytest.mark.parametrize("code, expected", [
    ("local x = 1 x += 2 x *= 5 x -= 1 x //= 2 return x", "7"),
    ("local s = 'a' s ..= 'b' s ..= 3 return s", "ab3"),
    ("local n = 0 for i = 1, 10 do if i % 2 == 0 then continue end n += i end return n", "25"),
    ("local n, i = 0, 0 while i < 5 do i += 1 if i == 3 then continue end n += 1 end return n", "4"),
    ("local t = {} for k, v in {a = 1, b = 2} do t[#t + 1] = k .. v end table.sort(t) return table.concat(t, ',')", "a1,b2"),
    ("local function it(t) return next, t, nil end local n = 0 for k, v in it({4, 5}) do n += v end return n", "9"),
    ("local n = 0 for _, v in ipairs({1, 2}) do n += v end for k in pairs({a = 1}) do n += #k end return n", "4"),
    ("local x = 5 return if x > 3 then 'big' elseif x > 1 then 'mid' else 'small'", "big"),
    ("local x = 2 return if x > 3 then 'big' elseif x > 1 then 'mid' else 'small'", "mid"),
    ("local t = {7} return (t :: any)[1]", "7"),
    ("type P = { a: number, b: string? }\nlocal p: P = { a = 1 } return p.a", "1"),
    ("export type Q<T> = { v: T }\nlocal function id<T>(v: T): T return v end return id(4)", "4"),
    ("local function f(a: number, ...: any): (number, ...any) return a + select('#', ...) end return f(1, 2, 3)", "3"),
    ("return 1_000 + 0b101", "1005"),
    ("return 'n=' .. 5", "n=5"),
    ("local f = function(): () end; (f)() return 'ok'", "ok"),
    ("local s = \"\\u{e9}\" return #s", "2"),
])
def test_translated_semantics(code, expected):
    assert run_luau(code) == expected


def test_continue_inside_nested_loops_targets_the_inner_loop():
    code = """
local out = {}
for i = 1, 3 do
  for j = 1, 3 do
    if j == 2 then continue end
    table.insert(out, i * 10 + j)
  end
end
return table.concat(out, ',')
"""
    assert run_luau(code) == "11,13,21,23,31,33"


def test_interpolated_strings_are_rejected_not_mistranslated():
    with pytest.raises(TranslateError):
        translate("local s = `hi {name}`")


def test_runtime_errors_point_at_the_original_luau_line():
    code = "local a = 1\n\n\nlocal b = nil\nreturn b.c\n"
    with pytest.raises(lua54.LuaError, match=r"snippet\.luau:5:"):
        run_luau(code)
