"""Contract test between the Python server and the Luau Studio plugin.

The two were written separately; this runs the real service over many
requests and checks that every action, value type and animation pose it
emits is one the plugin's ActionApplier / PropertyDecoder actually handles.
"""

import re
from pathlib import Path

import pytest

from godmode.anim import list_styles
from godmode.server.service import GodmodeService

PLUGIN = Path("plugin/src")
DECODER_TAGS = set(re.findall(r'tag == "(\w+)"', (PLUGIN / "PropertyDecoder.luau").read_text()))
HANDLED_ACTIONS = set(re.findall(r"^\t(\w+) = \w+,$", (PLUGIN / "ActionApplier.luau").read_text(), re.M))
R15_PARTS = {
    "HumanoidRootPart", "LowerTorso", "UpperTorso", "Head",
    "LeftUpperArm", "LeftLowerArm", "LeftHand", "RightUpperArm", "RightLowerArm", "RightHand",
    "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "RightUpperLeg", "RightLowerLeg", "RightFoot",
}

PROMPTS = [
    "build an obby with 8 red platforms", "make a house", "build a tower 5 floors", "make a staircase",
    "create an arena with spawn points", "make a tree", "build a car", "create a validated remote event for buying items",
    "add leaderstats with coins and gems", "save player data with datastores", "make a HUD with a health bar",
    "make an npc that patrols", "make a door that tweens open", "make a moving platform",
] + [f"make a {style} animation" for style in list_styles()]


@pytest.fixture(scope="module")
def service():
    return GodmodeService()


def _values(node, out):
    for v in node.get("properties", {}).values():
        out.append(v)
    for c in node.get("children", []):
        _values(c, out)


def test_plugin_sources_were_parsed():
    assert {"Vector3", "CFrame", "Color3", "UDim2"} <= DECODER_TAGS
    assert {"create_script", "create_instances", "create_animation", "delete_instance"} <= HANDLED_ACTIONS


@pytest.mark.parametrize("prompt", PROMPTS)
def test_every_emitted_action_is_applicable_by_the_plugin(service, prompt):
    reply = service.chat({"message": prompt, "autonomy": 3})
    assert reply["actions"], f"no actions for {prompt!r}: {reply['source']}"
    assert reply["source"].startswith("tool:")
    for action in reply["actions"]:
        assert action["type"] in HANDLED_ACTIONS
        if action["type"] == "create_instances":
            values = []
            _values(action["tree"], values)
            for v in values:
                if isinstance(v, dict):
                    if "$enum" in v:
                        assert re.fullmatch(r"\w+\.\w+", v["$enum"]), v
                    else:
                        assert v.get("$type") in DECODER_TAGS, v
        if action["type"] == "create_animation":
            hierarchy = action["hierarchy"]
            assert set(hierarchy) <= R15_PARTS
            for kf in action["keyframes"]:
                assert set(kf["poses"]) <= R15_PARTS
                for part in kf["poses"]:
                    parent = hierarchy.get(part)
                    assert parent is None or parent in R15_PARTS
        if action["type"] in ("create_script", "edit_script"):
            src = action.get("source") or action.get("new_source")
            assert "loadstring" not in src
