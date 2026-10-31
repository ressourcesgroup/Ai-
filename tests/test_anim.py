import json

import pytest

from godmode.anim import (HIERARCHY, JOINTS, POS_LIMITS, ROT_LIMITS, STYLES, generate, list_styles, player_script,
                          validate)
from godmode.luau import check_structure, lint

REQUIRED = {"idle", "walk", "run", "jump", "punch", "kick", "block", "hit", "knockdown", "victory", "wave", "shucks",
            "dance", "sit", "crouch"}


def test_all_required_styles_exist():
    assert REQUIRED <= {s["name"] for s in list_styles()}


def test_hierarchy_is_r15_part_tree():
    assert HIERARCHY["HumanoidRootPart"] is None
    assert HIERARCHY["LowerTorso"] == "HumanoidRootPart"
    assert HIERARCHY["Head"] == "UpperTorso"
    assert HIERARCHY["LeftFoot"] == "LeftLowerLeg" and HIERARCHY["LeftUpperLeg"] == "LowerTorso"
    assert HIERARCHY["RightHand"] == "RightLowerArm" and HIERARCHY["RightUpperArm"] == "UpperTorso"
    assert set(JOINTS) == set(HIERARCHY) - {"HumanoidRootPart"}
    assert JOINTS["LowerTorso"] == "Root" and JOINTS["UpperTorso"] == "Waist" and JOINTS["Head"] == "Neck"


@pytest.mark.parametrize("style", sorted(STYLES))
@pytest.mark.parametrize("speed,intensity", [(1.0, 1.0), (0.25, 1.5), (3.0, 0.5), (9.0, 9.0)])
def test_style_payload_valid(style, speed, intensity):
    p = generate(style, speed=speed, intensity=intensity)
    assert validate(p) == []
    assert p["type"] == "create_animation" and p["rig"] == "R15"
    json.dumps(p)  # JSON-encodable
    times = [k["time"] for k in p["keyframes"]]
    assert times[0] == 0 and all(b > a for a, b in zip(times, times[1:]))
    for k in p["keyframes"]:
        assert set(k["poses"]) == set(JOINTS)
        for part, pose in k["poses"].items():
            # every posed part chains up to the root through the hierarchy
            cur = part
            while HIERARCHY[cur] is not None:
                cur = HIERARCHY[cur]
            assert cur == "HumanoidRootPart"
            for axis in range(3):
                lo, hi = ROT_LIMITS[part][axis]
                assert lo <= pose["rotation"][axis] <= hi
                lo, hi = POS_LIMITS[part][axis]
                assert lo <= pose["position"][axis] <= hi
    if p["looped"]:
        assert p["keyframes"][0]["poses"] == p["keyframes"][-1]["poses"]


@pytest.mark.parametrize("style", sorted(STYLES))
def test_designed_poses_need_no_clamping_at_default_intensity(style):
    assert generate(style)["meta"]["clamped"] == 0


def test_speed_scales_duration_and_markers():
    a, b = generate("punch"), generate("punch", speed=2)
    assert b["duration"] == pytest.approx(a["duration"] / 2)
    assert [m["time"] for m in b["markers"]] == pytest.approx([m["time"] / 2 for m in a["markers"]])
    assert len(a["markers"]) == 3  # jab, cross, hook


def test_intensity_scales_angles():
    a, b = generate("wave", intensity=1.0), generate("wave", intensity=0.5)
    ra = a["keyframes"][0]["poses"]["RightUpperArm"]["rotation"][2]
    rb = b["keyframes"][0]["poses"]["RightUpperArm"]["rotation"][2]
    assert rb == pytest.approx(ra / 2)


def test_knockdown_ends_on_the_ground_and_run_leans_forward():
    k = generate("knockdown")["keyframes"][-1]["poses"]["LowerTorso"]
    assert k["position"][1] < -2 and abs(k["rotation"][0]) > 80
    run = generate("run")["keyframes"][0]["poses"]
    assert run["LowerTorso"]["rotation"][0] < -10 and run["RightLowerArm"]["rotation"][0] >= 80


def test_validate_catches_errors():
    p = generate("walk")
    p["keyframes"][-1]["poses"]["Head"]["rotation"][0] = 5.0
    assert any("same pose" in e for e in validate(p))
    p = generate("jump")
    p["keyframes"][1]["poses"]["LeftLowerLeg"]["rotation"][0] = 30.0  # knee hyper-extension
    assert validate(p)
    p = generate("jump")
    p["keyframes"][2]["time"] = 0.0
    assert any("increasing" in e for e in validate(p))


def test_unknown_style():
    with pytest.raises(KeyError):
        generate("moonwalk")


@pytest.mark.parametrize("style", sorted(STYLES))
def test_player_script_verified(style):
    p = generate(style)
    src = player_script(p["name"], style, p["looped"], p["priority"])
    assert check_structure(src).ok and lint(src) == []
    assert "Animator" in src and "Destroy()" in src and "UnbindAction" in src
