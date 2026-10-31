"""Build `create_animation` payloads (docs/PLUGIN_PROTOCOL.md) from procedural styles."""

from __future__ import annotations

import re

from .rig import HIERARCHY, JOINTS, POS_LIMITS, POSED_PARTS, ROT_LIMITS
from .styles import STYLES

EASING_STYLES = ("Linear", "Constant", "Cubic", "Elastic", "Bounce", "CubicV2")
EASING_DIRECTIONS = ("In", "Out", "InOut")
PRIORITIES = ("Core", "Idle", "Movement", "Action", "Action2", "Action3", "Action4")
PROVENANCE = ("procedural keyframes designed from the reference video TITLES supplied by the user "
              "(the videos were not watched; no motion capture)")

SPEED_RANGE = (0.25, 3.0)
INTENSITY_RANGE = (0.0, 1.5)


def list_styles() -> list[dict]:
    return [{"name": s.name, "description": s.description, "looped": s.looped, "duration": s.duration,
             "priority": s.priority, "keywords": list(s.keywords)} for s in STYLES.values()]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def generate(style: str, speed: float = 1.0, intensity: float = 1.0, name: str | None = None,
             parent_path: str = "ReplicatedStorage.GodmodeAnimations") -> dict:
    """Return a create_animation action payload for `style`.

    speed scales playback rate (times are divided by it); intensity scales every
    rotation and translation away from the rest pose. Both are clamped, and every
    resulting angle is clamped to the joint limits in rig.py (the number of clamped
    values is reported in `meta.clamped`).
    """
    if style not in STYLES:
        raise KeyError(f"unknown animation style {style!r}; known: {sorted(STYLES)}")
    st = STYLES[style]
    speed = _clamp(float(speed), *SPEED_RANGE)
    intensity = _clamp(float(intensity), *INTENSITY_RANGE)
    clamped = 0
    keyframes = []
    for key in st.keys:
        poses = {}
        for part in POSED_PARTS:
            src = key.pose.get(part, {"rotation": [0.0, 0.0, 0.0], "position": [0.0, 0.0, 0.0]})
            rot, pos = [], []
            for axis in range(3):
                r = src["rotation"][axis] * intensity
                lo, hi = ROT_LIMITS[part][axis]
                rc = _clamp(r, lo, hi)
                clamped += rc != r
                rot.append(round(rc, 2))
                p = src["position"][axis] * intensity
                lo, hi = POS_LIMITS[part][axis]
                pc = _clamp(p, lo, hi)
                clamped += pc != p
                pos.append(round(pc, 3))
            poses[part] = {"rotation": rot, "position": pos}
        keyframes.append({"time": round(key.time / speed, 4), "name": key.name, "easing_style": key.easing_style,
                          "easing_direction": key.easing_direction, "poses": poses})
    anim_name = name or ("Godmode" + st.name.capitalize())
    anim_name = re.sub(r"[^A-Za-z0-9_]", "", anim_name)[:50] or "GodmodeAnimation"
    return {
        "type": "create_animation",
        "description": f"Create {st.name} animation ({'looped' if st.looped else 'one-shot'}, "
                       f"{st.duration / speed:.2f}s)",
        "name": anim_name,
        "parent_path": parent_path,
        "rig": "R15",
        "looped": st.looped,
        "priority": st.priority,
        "duration": round(st.duration / speed, 4),
        "hierarchy": dict(HIERARCHY),
        "joints": dict(JOINTS),
        "keyframes": keyframes,
        "markers": [{**m, "time": round(m["time"] / speed, 4)} for m in st.markers],
        "style": st.name,
        "params": {"speed": speed, "intensity": intensity},
        "provenance": PROVENANCE,
        "destructive": False,
        "meta": {"clamped": clamped, "description": st.description},
    }


def validate(payload: dict) -> list[str]:
    """Structural checks on a create_animation payload. Empty list = valid."""
    errs: list[str] = []
    hier = payload.get("hierarchy", {})
    roots = [p for p, parent in hier.items() if parent is None]
    if roots != ["HumanoidRootPart"]:
        errs.append(f"hierarchy must have exactly one root HumanoidRootPart, got {roots}")
    for p, parent in hier.items():
        if parent is not None and parent not in hier:
            errs.append(f"{p}: parent {parent} missing from hierarchy")
        seen, cur = set(), p
        while cur is not None:
            if cur in seen:
                errs.append(f"cycle at {p}")
                break
            seen.add(cur)
            cur = hier.get(cur)
    if payload.get("priority") not in PRIORITIES:
        errs.append(f"bad priority {payload.get('priority')}")
    kfs = payload.get("keyframes", [])
    if len(kfs) < 2:
        errs.append("need at least 2 keyframes")
    times = [k["time"] for k in kfs]
    if times and times[0] != 0:
        errs.append("first keyframe must be at time 0")
    if any(b <= a for a, b in zip(times, times[1:])):
        errs.append("keyframe times must be strictly increasing")
    if times and abs(times[-1] - payload.get("duration", -1)) > 1e-6:
        errs.append("last keyframe time must equal duration")
    for k in kfs:
        if k.get("easing_style") not in EASING_STYLES or k.get("easing_direction") not in EASING_DIRECTIONS:
            errs.append(f"keyframe {k.get('name')}: bad easing")
        for part, pose in k["poses"].items():
            if part not in hier or part == "HumanoidRootPart" or part not in ROT_LIMITS:
                errs.append(f"keyframe {k.get('name')}: unknown part {part}")
                continue
            for axis in range(3):
                lo, hi = ROT_LIMITS[part][axis]
                if not lo - 1e-6 <= pose["rotation"][axis] <= hi + 1e-6:
                    errs.append(f"{part} rotation axis {axis} = {pose['rotation'][axis]} outside [{lo}, {hi}]")
                lo, hi = POS_LIMITS[part][axis]
                if not lo - 1e-6 <= pose["position"][axis] <= hi + 1e-6:
                    errs.append(f"{part} position axis {axis} = {pose['position'][axis]} outside [{lo}, {hi}]")
    if payload.get("looped") and kfs and kfs[0]["poses"] != kfs[-1]["poses"]:
        errs.append("looped animation must end on the same pose it starts with")
    for m in payload.get("markers", []):
        if times and not 0 <= m["time"] <= times[-1]:
            errs.append(f"marker {m['name']} outside the animation")
    return errs


def match_style(text: str) -> tuple[str | None, int]:
    """Best style for free text by keyword hits (returns (style, hits))."""
    t = text.lower()
    best, best_hits = None, 0
    for st in STYLES.values():
        hits = sum(1 for kw in st.keywords if re.search(rf"\b{re.escape(kw)}\b", t))
        if hits > best_hits:
            best, best_hits = st.name, hits
    return best, best_hits
