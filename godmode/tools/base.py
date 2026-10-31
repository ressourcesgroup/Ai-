"""Shared helpers for GODMODE's deterministic tool generators.

Tools are plain Python templates: given parsed parameters they emit protocol
actions (see docs/PLUGIN_PROTOCOL.md). Every Luau source they produce is checked
with godmode.luau (structure + lint) by :func:`verify_actions` before it can be
returned to a client.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from godmode.luau import check_structure, lint

# ---------------------------------------------------------------- properties


def v3(x: float, y: float, z: float) -> dict:
    return {"$type": "Vector3", "value": [_r(x), _r(y), _r(z)]}


def v2(x: float, y: float) -> dict:
    return {"$type": "Vector2", "value": [_r(x), _r(y)]}


def cf(pos: tuple[float, float, float], rot: tuple[float, float, float] = (0, 0, 0)) -> dict:
    return {"$type": "CFrame", "position": [_r(p) for p in pos], "rotation": [_r(a) for a in rot]}


def c3(rgb: tuple[int, int, int]) -> dict:
    return {"$type": "Color3", "rgb": [int(max(0, min(255, c))) for c in rgb]}


def udim2(xs: float, xo: float, ys: float, yo: float) -> dict:
    return {"$type": "UDim2", "value": [_r(xs), int(xo), _r(ys), int(yo)]}


def udim(scale: float, offset: float) -> dict:
    return {"$type": "UDim", "value": [_r(scale), int(offset)]}


def enum(path: str) -> dict:
    return {"$enum": path}


def brick(name: str) -> dict:
    return {"$type": "BrickColor", "name": name}


def _r(x: float) -> float:
    return round(float(x), 3)


def node(class_name: str, name: str, properties: dict | None = None, children: list | None = None,
         attributes: dict | None = None, tags: list[str] | None = None) -> dict:
    n = {"className": class_name, "name": name, "properties": dict(properties or {}), "children": list(children or [])}
    if attributes:
        n["attributes"] = dict(attributes)
    if tags:
        n["tags"] = list(tags)
    return n


def part(name: str, size, pos, color, material: str = "SmoothPlastic", rot=(0, 0, 0), shape: str | None = None,
         class_name: str = "Part", **extra) -> dict:
    """An anchored part. Size before CFrame (protocol rule)."""
    props: dict = {"Anchored": True, "Size": v3(*size), "CFrame": cf(pos, rot), "Color": c3(color),
                   "Material": enum(f"Material.{material}"), "TopSurface": enum("SurfaceType.Smooth"),
                   "BottomSurface": enum("SurfaceType.Smooth")}
    if shape:
        props["Shape"] = enum(f"PartType.{shape}")
    props.update(extra)
    return node(class_name, name, props)


# ---------------------------------------------------------------- actions


def create_script(parent_path: str, name: str, cls: str, source: str, description: str) -> dict:
    assert cls in ("Script", "LocalScript", "ModuleScript")
    return {"type": "create_script", "description": description, "parent_path": parent_path, "name": name,
            "class": cls, "source": source, "destructive": False}


def create_instances(parent_path: str, tree: dict, description: str) -> dict:
    return {"type": "create_instances", "description": description, "parent_path": parent_path, "tree": tree,
            "destructive": False}


def edit_script(path: str, new_source: str, description: str) -> dict:
    return {"type": "edit_script", "description": description, "path": path, "new_source": new_source,
            "destructive": False}


def delete_instance(path: str, description: str) -> dict:
    return {"type": "delete_instance", "description": description, "path": path, "destructive": True,
            "requires_approval": True}


# ---------------------------------------------------------------- verification


def verify_source(source: str, label: str) -> list[dict]:
    """Structure + lint checks for one Luau source, in protocol `verification` format."""
    res = check_structure(source)
    checks = [{"check": f"structure:{label}", "ok": res.ok,
               "detail": "balanced blocks/brackets, lexically valid" if res.ok else "; ".join(res.errors[:5])}]
    findings = lint(source)
    checks.append({"check": f"lint:{label}", "ok": not findings,
                   "detail": "no findings" if not findings else
                   "; ".join(f"{f.rule} line {f.line}: {f.message}" for f in findings[:5])})
    return checks


def verify_actions(actions: list[dict]) -> list[dict]:
    out: list[dict] = []
    for a in actions:
        if a["type"] == "create_script":
            out += verify_source(a["source"], f"{a['parent_path']}.{a['name']}")
        elif a["type"] == "edit_script":
            out += verify_source(a["new_source"], a["path"])
        elif a["type"] == "create_instances":
            problems = validate_tree(a["tree"])
            out.append({"check": f"tree:{a['parent_path']}.{a['tree']['name']}", "ok": not problems,
                        "detail": f"{count_nodes(a['tree'])} instance(s), property encoding valid" if not problems
                        else "; ".join(problems[:5])})
    return out


_SIMPLE = (int, float, str, bool)
_TYPED_KEYS = {"Vector3": ("value", 3), "Vector2": ("value", 2), "UDim2": ("value", 4), "UDim": ("value", 2),
               "Color3": ("rgb", 3), "NumberRange": ("value", 2)}


def validate_value(v) -> str | None:
    if isinstance(v, _SIMPLE):
        if isinstance(v, float) and not math.isfinite(v):
            return "non-finite number"
        return None
    if not isinstance(v, dict):
        return f"unsupported value {v!r}"
    if "$enum" in v:
        return None if re.fullmatch(r"[A-Za-z]+\.[A-Za-z0-9]+", str(v["$enum"])) else f"bad enum {v['$enum']!r}"
    t = v.get("$type")
    if t == "CFrame":
        ok = len(v.get("position", [])) == 3 and len(v.get("rotation", [])) == 3
        return None if ok else "bad CFrame"
    if t == "BrickColor":
        return None if isinstance(v.get("name"), str) else "bad BrickColor"
    if t in _TYPED_KEYS:
        key, n = _TYPED_KEYS[t]
        vals = v.get(key)
        if not isinstance(vals, list) or len(vals) != n or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vals):
            return f"bad {t}"
        return None
    return f"unknown $type {t!r}"


def validate_tree(tree: dict) -> list[str]:
    problems: list[str] = []
    stack = [tree]
    while stack:
        n = stack.pop()
        if not n.get("className") or not n.get("name") or "." in n["name"]:
            problems.append(f"node {n.get('name')!r} needs className and a dot-free name")
        for k, v in n.get("properties", {}).items():
            err = validate_value(v)
            if err:
                problems.append(f"{n.get('name')}.{k}: {err}")
        stack.extend(n.get("children", []))
    return problems


def count_nodes(tree: dict) -> int:
    return 1 + sum(count_nodes(c) for c in tree.get("children", []))


# ---------------------------------------------------------------- tool output


@dataclass
class ToolOutput:
    summary: str  # markdown explanation (without the source banner)
    actions: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra_verification: list[dict] = field(default_factory=list)


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- parameter parsing

COLORS: dict[str, tuple[int, int, int]] = {
    "red": (196, 40, 28), "green": (75, 151, 75), "blue": (13, 105, 172), "yellow": (245, 205, 48),
    "orange": (218, 133, 65), "purple": (107, 50, 124), "pink": (255, 102, 204), "white": (242, 243, 243),
    "black": (27, 42, 53), "gray": (163, 162, 165), "grey": (163, 162, 165), "brown": (124, 92, 70),
    "cyan": (4, 175, 236), "lime": (164, 189, 71), "gold": (239, 184, 56), "teal": (18, 238, 212),
}
MATERIALS = {"neon": "Neon", "wood": "Wood", "wooden": "Wood", "brick": "Brick", "concrete": "Concrete",
             "glass": "Glass", "metal": "Metal", "grass": "Grass", "plastic": "Plastic", "slate": "Slate",
             "marble": "Marble", "ice": "Ice", "sand": "Sand", "stone": "Slate", "granite": "Granite"}


def clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def parse_params(message: str) -> dict:
    """Extract simple numeric/color/material parameters from a chat message."""
    m = message.lower()
    p: dict = {}
    nouns = r"(platforms?|stages?|floors?|stor(?:y|ies)|levels?|steps?|stairs?|trees?|spawns?|spawn points?|waypoints?|points?|rooms?|kill ?bricks?|hits?)"
    found = re.search(r"(\d{1,4})\s*(?:\w+\s){0,2}?" + nouns, m)
    if found:
        p["count"] = int(found.group(1))
        p["count_noun"] = found.group(2)
    for key, pat in (("size", r"size\s*(?:of\s*)?(\d+(?:\.\d+)?)"), ("height", r"(?:height|tall)\s*(?:of\s*)?(\d+(?:\.\d+)?)"),
                     ("height", r"(\d+(?:\.\d+)?)\s*studs?\s*(?:high|tall)"), ("width", r"width\s*(?:of\s*)?(\d+(?:\.\d+)?)"),
                     ("spacing", r"spacing\s*(?:of\s*)?(\d+(?:\.\d+)?)"), ("spacing", r"(\d+(?:\.\d+)?)\s*studs?\s*apart"),
                     ("size", r"(\d+(?:\.\d+)?)\s*(?:x\s*\d+\s*)?studs?\s*(?:wide|big|across)"),
                     ("speed", r"speed\s*(?:of\s*)?(\d+(?:\.\d+)?)"), ("duration", r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)\b")):
        if key not in p:
            mm = re.search(pat, m)
            if mm:
                p[key] = float(mm.group(1))
    for name, rgb in COLORS.items():
        if re.search(rf"\b{name}\b", m):
            p["color"] = name
            p["rgb"] = rgb
            break
    for word, mat in MATERIALS.items():
        if re.search(rf"\b{word}\b", m):
            p["material"] = mat
            break
    if re.search(r"\b(fast|quick|rapid|snappy)\b", m):
        p["tempo"] = 1.4
    elif re.search(r"\b(slow|lazy|relaxed|sluggish)\b", m):
        p["tempo"] = 0.7
    if re.search(r"\b(exaggerated|big|huge|intense|strong|dramatic|cartoony)\b", m):
        p["intensity"] = 1.4
    elif re.search(r"\b(subtle|small|gentle|light|soft)\b", m):
        p["intensity"] = 0.6
    named = re.search(r"\b(?:called|named)\s+[\"']?([A-Za-z_][A-Za-z0-9_]{0,40})", message)
    if named:
        p["name"] = named.group(1)
    return p


def safe_identifier(name: str, default: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "", name or "")
    if not name or name[0].isdigit():
        return default
    return name[:40]


def lua_string(s: str) -> str:
    """A double-quoted Luau string literal (escaped)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
