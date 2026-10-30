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
