"""Structural tests for the Roblox Studio plugin (plugin/).

These cannot run the plugin (that needs Roblox Studio). They check that every
module is structurally valid Luau and lint-clean, that the .rbxmx packaging is
correct and lossless, that no dynamic-code-execution APIs appear in the plugin,
and that the plugin covers every value type and action type in
docs/PLUGIN_PROTOCOL.md.
"""

from __future__ import annotations

import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from godmode.luau import check_structure, lint

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
SRC = PLUGIN / "src"
PROTOCOL = ROOT / "docs" / "PLUGIN_PROTOCOL.md"
SOURCES = sorted(SRC.glob("*.luau"))


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_rbxmx", PLUGIN / "build_rbxmx.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def _protocol_text() -> str:
    if not PROTOCOL.exists():
        pytest.skip("docs/PLUGIN_PROTOCOL.md not present")
    return PROTOCOL.read_text(encoding="utf-8")


def test_expected_modules_exist():
    names = {p.stem for p in SOURCES}
    expected = {
        "Main", "ChatUI", "Context", "HttpClient", "ActionPreview", "ActionApplier",
        "PropertyDecoder", "PathResolver", "Markdown", "Theme", "Ui",
    }
    assert expected <= names, expected - names


# (a) structure + lint ---------------------------------------------------------

@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_module_structure_ok(path):
    result = check_structure(path.read_text(encoding="utf-8"))
    assert result.ok, result.errors


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_module_lint_clean(path):
    # No lint findings at all (errors or warnings); no exemptions are needed.
    findings = lint(path.read_text(encoding="utf-8"))
    assert findings == [], findings


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_module_is_strict(path):
    assert path.read_text(encoding="utf-8").startswith("--!strict\n")


# (b) packaging ----------------------------------------------------------------

@pytest.fixture(scope="module")
def built_tree(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist") / "GodmodePlugin.rbxmx"
    assert builder.main(["--out", str(out)]) == 0
    return ET.parse(out).getroot(), out


def _source_of(item) -> str:
    node = item.find("./Properties/ProtectedString[@name='Source']")
    assert node is not None
    return node.text or ""


def _name_of(item) -> str:
    node = item.find("./Properties/string[@name='Name']")
    assert node is not None
    return node.text or ""


def test_rbxmx_shape(built_tree):
    root, _ = built_tree
    assert root.tag == "roblox" and root.get("version") == "4"
    items = root.findall("Item")
    assert len(items) == 1
    main = items[0]
    assert main.get("class") == "Script"
    assert _name_of(main) == "GodmodePlugin"
    modules = main.findall("Item")
    assert all(m.get("class") == "ModuleScript" for m in modules)
    assert {_name_of(m) for m in modules} == {p.stem for p in SOURCES} - {"Main"}


def test_rbxmx_sources_round_trip(built_tree):
    root, _ = built_tree
    main = root.find("Item")
    assert _source_of(main) == (SRC / "Main.luau").read_text(encoding="utf-8")
    for module in main.findall("Item"):
        assert _source_of(module) == (SRC / f"{_name_of(module)}.luau").read_text(encoding="utf-8")


def test_rbxmx_referents_unique(built_tree):
    root, _ = built_tree
    refs = [item.get("referent") for item in root.iter("Item")]
    assert all(r and re.fullmatch(r"RBX[0-9A-F]{32}", r) for r in refs)
    assert len(refs) == len(set(refs)) == len(SOURCES)


def test_committed_dist_is_up_to_date():
    dist = PLUGIN / "dist" / "GodmodePlugin.rbxmx"
    assert dist.exists(), "run python3 plugin/build_rbxmx.py"
    assert dist.read_text(encoding="utf-8") == builder.build(SRC), "dist is stale; rebuild it"


def test_cdata_terminator_is_escaped(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    tricky = '--!strict\nlocal s = [[a]]..">"\nlocal t = "]]>" .. "]]]]>" -- & < >\n'
    (src / "Main.luau").write_text(tricky, encoding="utf-8")
    (src / "Helper.luau").write_text("return {']]>'}\r\n", encoding="utf-8")
    root = ET.fromstring(builder.build(src))
    main = root.find("Item")
    assert _source_of(main) == tricky
    assert _source_of(main.find("Item")) == "return {']]>'}\n"  # CRLF normalised


def test_requires_resolve_to_packaged_modules():
    modules = {p.stem for p in SOURCES} - {"Main"}
    main_src = (SRC / "Main.luau").read_text(encoding="utf-8")
    for name in re.findall(r"require\(script\.(\w+)\)", main_src):
        assert name in modules, name
    for path in SOURCES:
        if path.stem == "Main":
            continue
        for name in re.findall(r"require\(script\.Parent\.(\w+)\)", path.read_text(encoding="utf-8")):
            assert name in modules, f"{path.name} requires missing module {name}"


# (c) no dynamic code execution ------------------------------------------------

@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_dynamic_code_execution(path):
    src = path.read_text(encoding="utf-8")
    for banned in ("loadstring", "getfenv", "setfenv"):
        assert banned not in src, f"{path.name} contains {banned}"


def test_no_dynamic_code_execution_in_dist():
    dist = (PLUGIN / "dist" / "GodmodePlugin.rbxmx").read_text(encoding="utf-8")
    for banned in ("loadstring", "getfenv", "setfenv"):
        assert banned not in dist


# (d) protocol coverage --------------------------------------------------------

def _protocol_value_types(text: str) -> set[str]:
    types = set(re.findall(r'"\$type":\s*"(\w+)"', text))
    if "$enum" in text:
        types.add("$enum")
    return types


def _protocol_action_types(text: str) -> set[str]:
    return set(re.findall(r"^###\s+`(\w+)`", text, flags=re.MULTILINE)) - {"GET", "POST"}


def test_protocol_lists_expected_types():
    text = _protocol_text()
    assert {"Vector3", "CFrame", "Color3", "UDim2", "UDim", "BrickColor", "$enum"} <= _protocol_value_types(text)
    assert {"create_script", "edit_script", "create_instances", "create_animation", "delete_instance"} <= (
        _protocol_action_types(text)
    )


def test_decoder_supports_every_protocol_value_type():
    decoder = (SRC / "PropertyDecoder.luau").read_text(encoding="utf-8")
    for tag in _protocol_value_types(_protocol_text()):
        if tag == "$enum":
            assert 'encoded["$enum"]' in decoder
        else:
            assert f'tag == "{tag}"' in decoder, f"decoder does not handle $type {tag}"


def test_applier_handles_every_protocol_action():
    applier = (SRC / "ActionApplier.luau").read_text(encoding="utf-8")
    handlers = re.search(r"local HANDLERS[^{]*\{(.*?)\n\}", applier, flags=re.DOTALL)
    assert handlers, "HANDLERS table not found"
    handled = set(re.findall(r"^\s*(\w+)\s*=", handlers.group(1), flags=re.MULTILINE))
    missing = _protocol_action_types(_protocol_text()) - handled
    assert not missing, f"ActionApplier lacks handlers for {missing}"


def test_applier_uses_undo_recording_and_cancels_on_error():
    applier = (SRC / "ActionApplier.luau").read_text(encoding="utf-8")
    assert "TryBeginRecording" in applier
    assert "Enum.FinishRecordingOperation.Commit" in applier
    assert "Enum.FinishRecordingOperation.Cancel" in applier


def test_animation_uses_documented_rotation_orders():
    applier = (SRC / "ActionApplier.luau").read_text(encoding="utf-8")
    decoder = (SRC / "PropertyDecoder.luau").read_text(encoding="utf-8")
    # Pose: CFrame.Angles (X, then Y, then Z). Property CFrame: fromOrientation (Y, X, Z).
    assert "CFrame.Angles(math.rad(rotation.X), math.rad(rotation.Y), math.rad(rotation.Z))" in applier
    assert "CFrame.fromOrientation(math.rad(r[1]), math.rad(r[2]), math.rad(r[3]))" in decoder
    for enum_name in ("Enum.PoseEasingStyle", "Enum.PoseEasingDirection", "KeyframeMarker", "RegisterKeyframeSequence"):
        assert enum_name in applier


def test_http_client_uses_protocol_token_header():
    client = (SRC / "HttpClient.luau").read_text(encoding="utf-8")
    text = _protocol_text()
    header = re.search(r"header `([\w-]+):", text)
    assert header, "token header not found in protocol"
    assert f'headers["{header.group(1)}"]' in client


def test_path_resolver_refuses_internal_services():
    resolver = (SRC / "PathResolver.luau").read_text(encoding="utf-8")
    allowed = re.search(r"ALLOWED_SERVICES = \{(.*?)\}", resolver, flags=re.DOTALL)
    assert allowed
    names = set(re.findall(r"(\w+) = true", allowed.group(1)))
    assert "Workspace" in names and "ServerScriptService" in names
    assert not names & {"CoreGui", "CorePackages", "PluginGuiService", "RobloxReplicatedStorage", "PluginDebugService"}


# ---------------------------------------------------------------------------
# UI redesign: thinking levels, settings persistence, configured server URL
# ---------------------------------------------------------------------------

THINKING_IDS = ["low", "medium", "high", "xhigh", "max"]
THINKING_LABELS = ["Low", "Medium", "High", "Extra high", "Max"]


def _src(name: str) -> str:
    return (SRC / f"{name}.luau").read_text(encoding="utf-8")


def test_thinking_levels_are_exactly_the_five_ids():
    chat = _src("ChatUI")
    block = re.search(r"ChatUI\.THINKING_LEVELS = \{(.*?)\n\} :: ", chat, re.S)
    assert block, "ChatUI.THINKING_LEVELS table not found"
    entries = re.findall(r'\{ id = "(\w+)", label = "([^"]+)"', block.group(1))
    assert [e[0] for e in entries] == THINKING_IDS
    assert [e[1] for e in entries] == THINKING_LABELS
    assert 'ChatUI.DEFAULT_THINKING = "medium"' in chat


def test_chat_payload_sends_thinking_level():
    main = _src("Main")
    assert "thinking = thinkingLevel," in main
    # A stored value is only accepted when it is one of the five ids.
    assert "for _, level in ipairs(ChatUI.THINKING_LEVELS) do" in main


def test_http_client_uses_configured_base_url():
    http = _src("HttpClient")
    assert "self.baseUrl .. path" in http
    main = _src("Main")
    assert "HttpClient.new(serverUrl, token)" in main
    assert "DEFAULT_URL = \"http://127.0.0.1:8765\"" in main
    # Test connection hits <configured url>/health, not a hard-coded host.
    assert 'HttpClient.new(target, trim(testToken), 10):get("/health")' in main
    assert not re.search(r'post\("https?://', main)


@pytest.mark.parametrize(
    "key,name",
    [
        ("SETTING_URL", "GodmodeServerUrl"),
        ("SETTING_TOKEN", "GodmodeToken"),
        ("SETTING_AUTONOMY", "GodmodeAutonomy"),
        ("SETTING_THINKING", "GodmodeThinking"),
        ("SETTING_USE_SERVER", "godmode_use_server"),
    ],
)
def test_settings_are_persisted(key, name):
    main = _src("Main")
    assert f'local {key} = "{name}"' in main
    assert f"readSetting({key})" in main
    assert f"writeSetting({key}," in main


def test_offline_engine_returns_thinking_object():
    engine = _src("OfflineEngine")
    assert "thinking level has no effect without the server" in engine
    # Every return path of chat() attaches the trace.
    body = engine[engine.index("function OfflineEngine.chat"):]
    assert body.count("thinking = finish()") >= 3
    for field in ("level =", "summary =", "elapsed_ms =", "steps ="):
        assert field in engine


def test_thinking_block_only_when_present():
    chat = _src("ChatUI")
    assert 'if typeof(thinking) ~= "table" then\n\t\treturn nil' in chat
    assert "Thought for " in chat
