import pytest

from godmode.luau import check_structure, lint
from godmode.tools import TOOLS, ToolError, route, run_tool
from godmode.tools.base import parse_params, validate_tree, verify_actions

GEN_MESSAGES = {
    "remote_event": ["create a remote event called Attack", "make a remote event"],
    "leaderstats": ["set up leaderstats with coins and wins", "make a leaderboard"],
    "datastore": ["save player data with a datastore", "datastore for coins gems and level"],
    "ui_hud": ["make a working hud script with a health bar", "write a scripted gui with a currency label for gems"],
    "gui": ["make a hud with a health bar", "make a blue gui with a currency label for gems", "make a main menu with play and quit buttons"],
    "npc_patrol": ["npc patrol with pathfinding", "npc patrol with 6 waypoints spacing 50"],
    "tween_door": ["make a sliding door", "make a red door width 10 height 14"],
    "moving_platform": ["make a moving platform", "make an elevator that goes up 30 studs apart"],
    "build_house": ["build a house", "build a 3 floor wooden house size 40", "make a red room"],
    "build_obby": ["make an obby with 10 red platforms", "obby with 100 platforms spacing 20", "make a 2 stage obby"],
    "build_tower": ["build a 5 floor tower", "make a 30 floor tower size 80 height 30"],
    "build_staircase": ["make a staircase with 20 steps", "make a spiral staircase with 40 steps"],
    "build_arena": ["make an arena with 6 spawn points", "make a pvp map size 400"],
    "build_tree": ["make a low poly tree", "make 12 trees", "make a forest of 50 trees size 20"],
    "build_car": ["build a car", "build a blue car"],
    "animation": ["make a wave animation", "make a fast exaggerated sprint animation"],
}


@pytest.mark.parametrize("intent,message", [(i, m) for i, ms in GEN_MESSAGES.items() for m in ms])
def test_every_generator_output_is_verified(intent, message):
    r = route(message)
    assert r.name == intent, (message, r.name, r.scores)
    out, verification = run_tool(intent, r.params, message, {})
    assert out.actions, message
    assert verification and all(v["ok"] for v in verification), verification
    for a in out.actions:
        if a["type"] == "create_script":
            assert check_structure(a["source"]).ok
            assert lint(a["source"]) == []
            assert "not neural-model output" in a["source"]
            assert "." not in a["name"]
        if a["type"] == "create_instances":
            assert validate_tree(a["tree"]) == []
        assert a["destructive"] is False


def test_obby_params_and_server_side_kill_handler():
    msg = "make an obby with 7 red platforms spacing 15"
    out, _ = run_tool("build_obby", route(msg).params, msg)
    tree = out.actions[0]["tree"]
    plats = [c for c in tree["children"] if c["name"].startswith("Platform")]
    kills = [c for c in tree["children"] if c.get("tags") == ["GodmodeKillBrick"]]
    assert len(plats) == 7 and kills
    assert plats[0]["properties"]["Color"]["rgb"] == [196, 40, 28]
    xs = [p["properties"]["CFrame"]["position"][0] for p in plats]
    assert all(abs((b - a) - 15) < 1e-6 for a, b in zip(xs, xs[1:]))
    script = out.actions[1]
    assert script["parent_path"] == "ServerScriptService" and script["class"] == "Script"
    assert "GetPlayerFromCharacter" in script["source"] and "Disconnect" in script["source"]


def test_counts_are_clamped():
    msg = "make an obby with 5000 platforms"
    out, _ = run_tool("build_obby", route(msg).params, msg)
    assert len([c for c in out.actions[0]["tree"]["children"] if c["name"].startswith("Platform")]) == 100


def test_remote_event_is_validated_and_rate_limited():
    out, _ = run_tool("remote_event", {"name": "Attack"}, "remote event called Attack")
    server = out.actions[0]["source"]
    for needle in ("typeof(action)", "isRateLimited", "PlayerRemoving", "amount ~= amount"):
        assert needle in server
    assert out.actions[1]["class"] == "LocalScript"


def test_datastore_uses_update_async_session_lock_and_bind_to_close():
    out, _ = run_tool("datastore", {}, "datastore")
    src = out.actions[0]["source"]
    assert "UpdateAsync" in src and "SetAsync" not in src and "BindToClose" in src and "game.JobId" in src
    assert "pcall" in src


def test_hud_uses_scale_and_aspect_ratio():
    out, _ = run_tool("ui_hud", {}, "hud")
    tree = out.actions[0]["tree"]
    classes = []

    def walk(n):
        classes.append(n["className"])
        for c in n["children"]:
            walk(c)
    walk(tree)
    assert "UIAspectRatioConstraint" in classes and "TextButton" in classes
    assert tree["children"][0]["properties"]["Size"]["value"][1] == 0  # no pixel offset


def test_verification_failure_returns_error_not_code(monkeypatch):
    from godmode.tools import scripts

    def bad(params, ctx):
        from godmode.tools.base import ToolOutput, create_script
        return ToolOutput("x", [create_script("ServerScriptService", "Bad", "Script", "while true do\nwait(1)\n", "bad")])

    monkeypatch.setitem(TOOLS, "leaderstats", bad)
    with pytest.raises(ToolError) as ei:
        run_tool("leaderstats", {}, "leaderstats")
    assert any(not v["ok"] for v in ei.value.verification)
    assert scripts  # keep import used


def test_delete_selected_is_destructive():
    ctx = {"selection": [{"path": "Workspace.OldMap", "className": "Model", "name": "OldMap"}]}
    out, _ = run_tool("delete_selected", {}, "delete the selected model", ctx)
    assert out.actions[0]["type"] == "delete_instance" and out.actions[0]["destructive"] is True
    with pytest.raises(ToolError):
        run_tool("delete_selected", {}, "delete", {"selection": []})


def test_script_review_proposes_verified_fix():
    ctx = {"openScript": {"path": "ServerScriptService.Old", "source": "while true do\n\twait(1)\nend\n"}}
    out, verification = run_tool("script_review", {}, "review my script", ctx)
    assert any(not v["ok"] for v in verification)  # the user's script has a GM006 finding
    edit = out.actions[0]
    assert edit["type"] == "edit_script" and "task.wait(1)" in edit["new_source"]
    assert verify_actions([edit])[0]["ok"]


def test_parse_params():
    p = parse_params("make an obby with 10 red neon platforms size 8, 20 studs apart, fast")
    assert p["count"] == 10 and p["color"] == "red" and p["material"] == "Neon" and p["size"] == 8
    assert p["spacing"] == 20 and p["tempo"] > 1
    assert parse_params("remote called Attack!")["name"] == "Attack"


ROUTING = [
    ("make an obby with 10 platforms", "build_obby"),
    ("build me an obstacle course", "build_obby"),
    ("create a house with 2 floors", "build_house"),
    ("make a tall tower", "build_tower"),
    ("add a staircase", "build_staircase"),
    ("generate an arena map with spawn points", "build_arena"),
    ("place some low poly trees", "build_tree"),
    ("build a sports car", "build_car"),
    ("make a hud with health bar and coins", "gui"),
    ("make a working hud script with health bar", "ui_hud"),
    ("i need an npc that patrols with pathfinding", "npc_patrol"),
    ("add a door that opens", "tween_door"),
    ("create a moving platform", "moving_platform"),
    ("set up leaderstats", "leaderstats"),
    ("save player coins with datastore", "datastore"),
    ("make a remote event for attacking", "remote_event"),
    ("make an idle breathing animation", "animation"),
    ("walk cycle animation", "animation"),
    ("make my character sprint like a fugitive chase", "animation"),
    ("jump animation", "animation"),
    ("punch combo animation", "animation"),
    ("kick animation", "animation"),
    ("block animation", "animation"),
    ("hit reaction stagger animation", "animation"),
    ("squid game elimination fall animation", "animation"),
    ("victory pose emote", "animation"),
    ("wave emote", "animation"),
    ("aw shucks sheepish emote", "animation"),
    ("dance animation loop", "animation"),
    ("sit animation", "animation"),
    ("crouch sneak animation", "animation"),
    ("how do I use TweenService?", "question"),
    ("what is a ModuleScript", "question"),
    ("why does my datastore fail", "question"),
    ("write a function that shuffles a table", "freeform_code"),
    ("help", "help"),
]


@pytest.mark.parametrize("message,intent", ROUTING)
def test_intent_routing(message, intent):
    r = route(message)
    assert r.name == intent, (message, r.name, r.scores)
    assert 0 <= r.confidence <= 1
    assert r.router == "keyword-regex"


@pytest.mark.parametrize("message,style", [
    ("idle breathing animation", "idle"), ("walk cycle animation", "walk"), ("fugitive chase run animation", "run"),
    ("jump animation", "jump"), ("punch combo animation", "punch"), ("kick animation", "kick"),
    ("block animation", "block"), ("hit reaction animation", "hit"), ("squid game elimination animation", "knockdown"),
    ("victory animation", "victory"), ("wave animation", "wave"), ("aw shucks emote", "shucks"),
    ("dance animation", "dance"), ("sit animation", "sit"), ("crouch sneak animation", "crouch"),
])
def test_animation_style_routing(message, style):
    r = route(message)
    assert r.name == "animation" and r.params["style"] == style


def test_review_and_delete_need_context():
    assert route("review my script").name != "script_review"
    assert route("review my script", {"openScript": {"path": "A.B", "source": "x"}}).name == "script_review"
    sel = {"selection": [{"path": "Workspace.Part", "className": "Part", "name": "Part"}]}
    assert route("delete the selected part", sel).name == "delete_selected"
