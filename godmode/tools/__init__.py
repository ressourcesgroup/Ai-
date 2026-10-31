"""GODMODE deterministic tool generators + intent router.

`run_tool(intent, message, context)` returns a ToolOutput whose Luau has been
verified (structure + lint); if verification fails a ToolError is raised and the
code is NOT returned (the caller reports the failing checks).
"""

from __future__ import annotations

import re

from godmode.anim import PROVENANCE, generate as generate_animation, list_styles, player_script, validate as validate_animation

from . import builders, gui, scripts
from .base import ToolError, ToolOutput, create_script, delete_instance, edit_script, verify_actions, verify_source
from .router import ROUTER_NAME, TOOL_THRESHOLD, Intent, route


def animation_tool(params: dict, ctx: dict) -> ToolOutput:
    style = params.get("style")
    if not style:
        raise ToolError("no animation style recognised")
    speed = float(params.get("tempo", 1.0))
    intensity = float(params.get("intensity", 1.0))
    payload = generate_animation(style, speed=speed, intensity=intensity)
    problems = validate_animation(payload)
    src = player_script(payload["name"], style, payload["looped"], payload["priority"])
    extra = [{"check": f"animation:{payload['name']}", "ok": not problems,
              "detail": f"{len(payload['keyframes'])} keyframes, joint limits respected, hierarchy consistent"
              + (", loop closed" if payload["looped"] else "") if not problems else "; ".join(problems[:5])}]
    if problems:
        return ToolOutput("", [], [], extra)
    styles = {s["name"]: s for s in list_styles()}
    return ToolOutput(
        summary=(f"**{style}** animation `{payload['name']}` ({'looped' if payload['looped'] else 'one-shot'}, "
                 f"{payload['duration']:.2f} s, priority {payload['priority']}, speed x{payload['params']['speed']:g}, "
                 f"intensity x{payload['params']['intensity']:g}): {styles[style]['description']}\n\n"
                 f"Keyframes: {', '.join(k['name'] for k in payload['keyframes'])}.\n\n"
                 "The plugin builds a KeyframeSequence in `ReplicatedStorage.GodmodeAnimations`; the LocalScript in "
                 "`StarterCharacterScripts` toggles it with **G** (Studio test via a temporary id; publish it and paste "
                 "the asset id for live games).\n\n"
                 f"_Provenance: {PROVENANCE}._"),
        actions=[payload, create_script("StarterPlayer.StarterCharacterScripts", f"Play{payload['name']}", "LocalScript",
                                        src, f"Play {payload['name']} on the local character")],
        extra_verification=extra)


def delete_selected(params: dict, ctx: dict) -> ToolOutput:
    sel = (ctx.get("context") or {}).get("selection") or []
    paths = [s["path"] for s in sel if isinstance(s, dict) and isinstance(s.get("path"), str) and s["path"]]
    if not paths:
        raise ToolError("nothing is selected in Studio; select the instance(s) to delete first")
    if any(p.count(".") == 0 for p in paths):
        raise ToolError("refusing to propose deleting a whole service")
    return ToolOutput(
        summary="Proposed deletion of: " + ", ".join(f"`{p}`" for p in paths) +
                ". These are **destructive** and always need your approval (undo is available in Studio).",
        actions=[delete_instance(p, f"Delete {p}") for p in paths])


_DEPRECATED = re.compile(r"(?<![\w.:])(wait|spawn|delay)(\s*\()")


def script_review(params: dict, ctx: dict) -> ToolOutput:
    open_script = (ctx.get("context") or {}).get("openScript") or {}
    src = open_script.get("source")
    path = open_script.get("path") or "OpenScript"
    if not isinstance(src, str):
        raise ToolError("no open script was sent; open the script in Studio first")
    checks = verify_source(src, path)
    lines = [f"- {c['check']}: {'OK' if c['ok'] else 'FAIL'} - {c['detail']}" for c in checks]
    actions = []
    fixed = _DEPRECATED.sub(r"task.\1\2", src)
    note = ""
    if fixed != src:
        if all(c["ok"] for c in verify_source(fixed, path)):
            actions.append(edit_script(path, fixed, "Replace deprecated wait/spawn/delay with task.* equivalents"))
            note = "\n\nProposed a mechanical fix: deprecated `wait/spawn/delay` -> `task.wait/spawn/delay`."
        else:
            note = "\n\nA `wait/spawn/delay` -> `task.*` rewrite alone would not make the script pass, so no edit is proposed."
    return ToolOutput(summary=f"Checks on `{path}` (heuristic structural check + GODMODE lint rules):\n\n" +
                      "\n".join(lines) + note + "\n\nOther findings need a manual fix; the checker means "
                      "'plausible', not 'compiles'.", actions=actions, extra_verification=checks)


TOOLS = {
    "remote_event": scripts.remote_event,
    "leaderstats": scripts.leaderstats,
    "datastore": scripts.datastore,
    "ui_hud": scripts.ui_hud,
    "gui": gui.gui_design,
    "npc_patrol": scripts.npc_patrol,
    "tween_door": scripts.tween_door,
    "moving_platform": scripts.moving_platform,
    "build_house": builders.build_house,
    "build_obby": builders.build_obby,
    "build_tower": builders.build_tower,
    "build_staircase": builders.build_staircase,
    "build_arena": builders.build_arena,
    "build_tree": builders.build_tree,
    "build_car": builders.build_car,
    "animation": animation_tool,
    "delete_selected": delete_selected,
    "script_review": script_review,
}

# Short names used in the `tool:<name>` source tag.
TOOL_TAGS = {k: k.removeprefix("build_") if k.startswith("build_") else k for k in TOOLS}


def run_tool(intent: str, params: dict, message: str = "", context: dict | None = None) -> tuple[ToolOutput, list[dict]]:
    """Run a tool and verify its output. Returns (output, verification). Raises ToolError on failure."""
    fn = TOOLS[intent]
    out = fn(params, {"message": message, "context": context or {}})
    generated = verify_actions(out.actions)
    verification = list(out.extra_verification) + generated
    # For script_review the user's own script (extra_verification) may fail; only what WE generate must pass.
    must_pass = generated if intent == "script_review" else verification
    failed = [v for v in must_pass if not v["ok"]]
    if failed:
        err = ToolError(f"generated output failed verification: {failed[0]['check']}")
        err.verification = verification  # type: ignore[attr-defined]
        raise err
    return out, verification


__all__ = ["TOOLS", "TOOL_TAGS", "run_tool", "route", "Intent", "ToolOutput", "ToolError", "ROUTER_NAME",
           "TOOL_THRESHOLD", "verify_source", "verify_actions"]
