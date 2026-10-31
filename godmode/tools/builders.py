"""Parametric builders that emit `create_instances` trees (plus server scripts where needed).

All geometry is computed here in Python; the plugin only instantiates it.
Parameters are clamped so a chat message can never request a million parts.
"""

from __future__ import annotations

import math
import random

from .base import ToolOutput, c3, create_instances, create_script, enum, node, part, v3
from .scripts import HEADER, fill


def _num(params: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(params.get(key, default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _count(params: dict, default: int, lo: int, hi: int, nouns: tuple[str, ...] = ()) -> int:
    if "count" in params and (not nouns or params.get("count_noun", "").startswith(nouns)):
        return max(lo, min(hi, int(params["count"])))
    return default


def _color(params: dict, default: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(params.get("rgb", default))  # type: ignore[return-value]


# ------------------------------------------------------------------ house

def build_house(params: dict, ctx: dict) -> ToolOutput:
    w = _num(params, "size", 24, 12, 120)
    d = _num(params, "width", w * 0.8, 10, 120)
    h = _num(params, "height", 12, 8, 40)
    floors = _count(params, 1, 1, 5, ("floor", "stor", "level"))
    color = _color(params, (230, 220, 200))
    mat = params.get("material", "Brick" if "rgb" not in params else "SmoothPlastic")
    t = 1.0
    door_w, door_h = 5.0, 8.0
    kids = [part("Foundation", (w + 2, 1, d + 2), (0, 0.5, 0), (120, 120, 120), "Concrete")]
    for f in range(floors):
        base = 1 + f * h
        y = base + h / 2
        fl = f"F{f + 1}"
        if f > 0:
            kids.append(part(f"{fl}Floor", (w, t, d), (0, base + t / 2, 0), (150, 120, 90), "WoodPlanks"))
        kids += [
            part(f"{fl}WallBack", (w, h, t), (0, y, -d / 2 + t / 2), color, mat),
            part(f"{fl}WallLeft", (t, h, d - 2 * t), (-w / 2 + t / 2, y, 0), color, mat),
            part(f"{fl}WallRight", (t, h, d - 2 * t), (w / 2 - t / 2, y, 0), color, mat),
        ]
        side = (w - door_w) / 2
        if f == 0:
            kids += [
                part(f"{fl}WallFrontLeft", (side, h, t), (-(door_w / 2 + side / 2), y, d / 2 - t / 2), color, mat),
                part(f"{fl}WallFrontRight", (side, h, t), (door_w / 2 + side / 2, y, d / 2 - t / 2), color, mat),
                part(f"{fl}WallFrontTop", (door_w, h - door_h, t), (0, base + door_h + (h - door_h) / 2, d / 2 - t / 2), color, mat),
            ]
        else:
            kids.append(part(f"{fl}WallFront", (w, h, t), (0, y, d / 2 - t / 2), color, mat))
        kids += [
            part(f"{fl}WindowLeft", (0.2, h * 0.35, d * 0.3), (-w / 2 - 0.05, y + 1, 0), (170, 220, 255), "Glass",
                 Transparency=0.4, CanCollide=False),
            part(f"{fl}WindowRight", (0.2, h * 0.35, d * 0.3), (w / 2 + 0.05, y + 1, 0), (170, 220, 255), "Glass",
                 Transparency=0.4, CanCollide=False),
        ]
    top = 1 + floors * h
    kids.append(part("Roof", (w + 3, 1, d + 3), (0, top + 0.5, 0), (110, 60, 50), "Slate"))
    roof_h = min(w, d) * 0.3
    kids.append(part("RoofPeakFront", (w + 3, roof_h, (d + 3) / 2), (0, top + 1 + roof_h / 2, (d + 3) / 4), (110, 60, 50),
                     "Slate", class_name="WedgePart"))
    kids.append(part("RoofPeakBack", (w + 3, roof_h, (d + 3) / 2), (0, top + 1 + roof_h / 2, -(d + 3) / 4), (110, 60, 50),
                     "Slate", rot=(0, 180, 0), class_name="WedgePart"))
    tree = node("Model", "GodmodeHouse", {}, kids)
    return ToolOutput(
        summary=f"A {floors}-floor house, {w:g} x {d:g} studs, {h:g}-stud walls ({mat}), door opening in front, glass "
                f"side windows and a pitched wedge roof. {len(kids)} anchored parts in `Workspace.GodmodeHouse`.",
        actions=[create_instances("Workspace", tree, "Build house")])


# ------------------------------------------------------------------ obby

KILL_SCRIPT = """{{HEADER}}-- Server-side kill bricks: any part tagged "GodmodeKillBrick" resets the character that touches it.
local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")

local TAG = "GodmodeKillBrick"
local connections = {}
local recentlyKilled = {}

local function onTouched(hit: BasePart)
	local character = hit.Parent
	if character == nil or recentlyKilled[character] then
		return
	end
	local humanoid = character:FindFirstChildOfClass("Humanoid")
	if humanoid == nil or humanoid.Health <= 0 then
		return
	end
	if Players:GetPlayerFromCharacter(character) == nil then
		return
	end
	recentlyKilled[character] = true
	humanoid.Health = 0
	task.delay(1, function()
		recentlyKilled[character] = nil
	end)
end

local function register(instance: Instance)
	if not instance:IsA("BasePart") or connections[instance] then
		return
	end
	connections[instance] = instance.Touched:Connect(onTouched)
end

local function unregister(instance: Instance)
	local connection = connections[instance]
	if connection then
		connection:Disconnect()
		connections[instance] = nil
	end
end

CollectionService:GetInstanceAddedSignal(TAG):Connect(register)
CollectionService:GetInstanceRemovedSignal(TAG):Connect(unregister)
for _, instance in ipairs(CollectionService:GetTagged(TAG)) do
	register(instance)
end
"""


def build_obby(params: dict, ctx: dict) -> ToolOutput:
    n = _count(params, 10, 2, 100, ("platform", "stage", "step", "level"))
    spacing = _num(params, "spacing", 12, 6, 40)
    size = _num(params, "size", 6, 3, 30)
    color = _color(params, (13, 105, 172))
    rng = random.Random(n * 1000 + int(spacing))
    kids = [node("SpawnLocation", "Start", {"Anchored": True, "Size": v3(12, 1, 12), "CFrame": {"$type": "CFrame",
            "position": [0, 0.5, 0], "rotation": [0, 0, 0]}, "Neutral": True, "Duration": 0,
            "Color": c3((75, 151, 75)), "Material": enum("Material.SmoothPlastic")})]
    x = 0.0
    y = 0.5
    kills = 0
    for i in range(1, n + 1):
        x += spacing
        y += rng.choice((0.0, 1.0, 2.0))
        z = rng.uniform(-3, 3)
        kids.append(part(f"Platform{i:03d}", (size, 1, size), (x, y, z), color, params.get("material", "SmoothPlastic")))
        if i % 2 == 0 and i < n:
            kills += 1
            kids.append({**part(f"KillBrick{kills:03d}", (2, 1, size), (x + spacing / 2, y - 3, z), (255, 0, 0), "Neon"),
                         "tags": ["GodmodeKillBrick"]})
    x += spacing
    kids.append(part("Finish", (12, 1, 12), (x, y, 0), (245, 205, 48), "Neon"))
    tree = node("Model", "GodmodeObby", {}, kids)
    src = fill(KILL_SCRIPT, HEADER=HEADER.format(tool="tool:obby"))
    return ToolOutput(
        summary=f"An obby with a spawn, **{n} platforms** ({size:g}-stud, {spacing:g} studs apart, gently rising), "
                f"{kills} neon kill bricks tagged `GodmodeKillBrick`, and a finish pad. Kills are handled **on the server** "
                "by a CollectionService script (players only, per-character debounce, connections cleaned up when a "
                "brick is removed).",
        actions=[create_instances("Workspace", tree, "Build obby"),
                 create_script("ServerScriptService", "GodmodeKillBricks", "Script", src, "Kill brick handler")])


# ------------------------------------------------------------------ tower

def build_tower(params: dict, ctx: dict) -> ToolOutput:
    floors = _count(params, 5, 1, 30, ("floor", "stor", "level"))
    size = _num(params, "size", 20, 8, 80)
    fh = _num(params, "height", 12, 6, 30)
    color = _color(params, (163, 162, 165))
    mat = params.get("material", "Concrete")
    kids = []
    for f in range(floors):
        base = f * fh
        kids.append(part(f"Floor{f + 1:02d}", (size, 1, size), (0, base + 0.5, 0), color, mat))
        for cx in (-1, 1):
            for cz in (-1, 1):
                kids.append(part(f"Pillar{f + 1:02d}_{'L' if cx < 0 else 'R'}{'B' if cz < 0 else 'F'}",
                                 (2, fh - 1, 2), (cx * (size / 2 - 1), base + 1 + (fh - 1) / 2, cz * (size / 2 - 1)), color, mat))
    kids.append(part("Roof", (size + 2, 1, size + 2), (0, floors * fh + 0.5, 0), color, mat))
    total = floors * fh
    kids.append(node("TrussPart", "Ladder", {"Anchored": True, "Size": v3(2, total, 2),
                                             "CFrame": {"$type": "CFrame", "position": [0, total / 2 + 1, size / 2 + 1],
                                                        "rotation": [0, 0, 0]},
                                             "Color": c3((90, 90, 90)), "Material": enum("Material.Metal")}))
    tree = node("Model", "GodmodeTower", {}, kids)
    return ToolOutput(
        summary=f"A {floors}-floor open tower ({size:g} x {size:g}, {fh:g} studs per floor, {mat}) with corner pillars, "
                "a roof and a truss ladder on the front face for climbing.",
        actions=[create_instances("Workspace", tree, "Build tower")])


# ------------------------------------------------------------------ staircase

def build_staircase(params: dict, ctx: dict) -> ToolOutput:
    n = _count(params, 12, 2, 200, ("step", "stair"))
    width = _num(params, "width", 6, 2, 40)
    rise = 1.0
    run = 2.0
    color = _color(params, (163, 162, 165))
    mat = params.get("material", "Concrete")
    spiral = "spiral" in ctx.get("message", "").lower()
    kids = []
    radius = _num(params, "size", 8, 4, 60)
    for i in range(n):
        y = rise * i + rise / 2
        if spiral:
            ang = i * 20.0
            a = math.radians(ang)
            kids.append(part(f"Step{i + 1:03d}", (width, rise, run * 1.5), (radius * math.sin(a), y, radius * math.cos(a)),
                             color, mat, rot=(0, ang + 90, 0)))
        else:
            kids.append(part(f"Step{i + 1:03d}", (width, rise, run), (0, y, -run * i), color, mat))
    if spiral:
        kids.append(part("Column", (3, rise * n + 2, 3), (0, (rise * n + 2) / 2, 0), color, mat, shape="Cylinder",
                         rot=(0, 0, 90)))
    tree = node("Model", "GodmodeStaircase", {}, kids)
    return ToolOutput(
        summary=f"A {'spiral' if spiral else 'straight'} staircase with {n} steps ({rise:g}-stud rise, {width:g} wide).",
        actions=[create_instances("Workspace", tree, "Build staircase")])


# ------------------------------------------------------------------ arena / map

def build_arena(params: dict, ctx: dict) -> ToolOutput:
    size = _num(params, "size", 120, 40, 600)
    spawns = _count(params, 8, 2, 32, ("spawn", "point"))
    color = _color(params, (99, 95, 98))
    wall_h = 14.0
    kids = [part("Floor", (size, 2, size), (0, -1, 0), color, params.get("material", "Slate")),
            part("CenterPlatform", (size * 0.15, 2, size * 0.15), (0, 1, 0), (245, 205, 48), "Neon")]
    for name, sx, sz, px, pz in (("WallNorth", size, 2, 0, -size / 2), ("WallSouth", size, 2, 0, size / 2),
                                 ("WallWest", 2, size, -size / 2, 0), ("WallEast", 2, size, size / 2, 0)):
        kids.append(part(name, (sx, wall_h, sz), (px, wall_h / 2, pz), (60, 60, 70), "Concrete"))
    spawn_nodes = []
    r = size * 0.38
    for i in range(spawns):
        a = 2 * math.pi * i / spawns
        x, z = r * math.cos(a), r * math.sin(a)
        facing = math.degrees(math.atan2(x, z))  # face the centre
        spawn_nodes.append(node("SpawnLocation", f"Spawn{i + 1:02d}", {
            "Anchored": True, "Size": v3(6, 1, 6),
            "CFrame": {"$type": "CFrame", "position": [round(x, 3), 0.5, round(z, 3)], "rotation": [0, round(facing, 3), 0]},
            "Neutral": True, "Duration": 3, "Color": c3((4, 175, 236)), "Material": enum("Material.SmoothPlastic")}))
    rng = random.Random(int(size))
    for i in range(6):
        a = rng.uniform(0, 2 * math.pi)
        rr = rng.uniform(size * 0.15, size * 0.3)
        kids.append(part(f"Cover{i + 1}", (8, 6, 2), (rr * math.cos(a), 3, rr * math.sin(a)), (120, 120, 120), "Concrete",
                         rot=(0, math.degrees(a), 0)))
    kids.append(node("Folder", "SpawnPoints", {}, spawn_nodes))
    tree = node("Model", "GodmodeArena", {}, kids)
    return ToolOutput(
        summary=f"A {size:g} x {size:g} walled arena with a neon centre platform, 6 cover walls and {spawns} SpawnLocations "
                "in a ring, each rotated to face the centre.",
        actions=[create_instances("Workspace", tree, "Build arena")])


# ------------------------------------------------------------------ low-poly tree

def _one_tree(name: str, x: float, z: float, scale: float, leaf_rgb, rng: random.Random) -> dict:
    trunk_h = 8 * scale
    kids = [part("Trunk", (trunk_h, 1.6 * scale, 1.6 * scale), (x, trunk_h / 2, z), (105, 64, 40), "Wood",
                 shape="Cylinder", rot=(0, 0, 90))]
    for k, (s, dy) in enumerate(((7.0, 0.0), (5.5, 2.6), (3.5, 4.8))):
        kids.append(part(f"Leaves{k + 1}", (s * scale, 2.8 * scale, s * scale), (x, trunk_h + dy * scale, z), leaf_rgb,
                         "SmoothPlastic", rot=(rng.uniform(-8, 8), 45 * k + rng.uniform(0, 20), rng.uniform(-8, 8))))
    return node("Model", name, {}, kids)


def build_tree(params: dict, ctx: dict) -> ToolOutput:
    n = _count(params, 1, 1, 50, ("tree",))
    scale = _num(params, "size", 1, 0.3, 5) if params.get("size", 0) <= 5 else _num(params, "size", 10, 3, 50) / 10
    leaf = _color(params, (75, 151, 75))
    rng = random.Random(n)
    trees = []
    for i in range(n):
        x, z = (0.0, 0.0) if n == 1 else (rng.uniform(-6, 6) * math.sqrt(n), rng.uniform(-6, 6) * math.sqrt(n))
        trees.append(_one_tree(f"Tree{i + 1:02d}", round(x, 2), round(z, 2), scale * rng.uniform(0.85, 1.15), leaf, rng))
    tree = trees[0] if n == 1 else node("Model", "GodmodeTrees", {}, trees)
    if n == 1:
        tree["name"] = "GodmodeTree"
    return ToolOutput(
        summary=f"{n} low-poly tree{'s' if n > 1 else ''}: cylinder trunk and three stacked, rotated leaf blocks "
                "(deterministic jitter so they don't look identical).",
        actions=[create_instances("Workspace", tree, "Build low-poly tree(s)")])


# ------------------------------------------------------------------ car body

def build_car(params: dict, ctx: dict) -> ToolOutput:
    body = _color(params, (196, 40, 28))
    L, W = 14.0, 7.0
    kids = [
        part("Chassis", (W, 1.2, L), (0, 1.8, 0), (40, 40, 40), "Metal"),
        part("Body", (W, 2.2, L - 1), (0, 3.5, 0), body, "SmoothPlastic"),
        part("Cabin", (W - 0.6, 2.4, L * 0.45), (0, 5.8, -0.8), body, "SmoothPlastic"),
        part("Windshield", (W - 1.0, 2.0, 0.2), (0, 5.8, 2.0), (170, 220, 255), "Glass", Transparency=0.35),
        part("RearWindow", (W - 1.0, 2.0, 0.2), (0, 5.8, -3.95), (170, 220, 255), "Glass", Transparency=0.35),
        part("HeadlightLeft", (1.2, 0.8, 0.2), (-W / 2 + 1, 3.8, L / 2 - 0.4), (255, 255, 200), "Neon"),
        part("HeadlightRight", (1.2, 0.8, 0.2), (W / 2 - 1, 3.8, L / 2 - 0.4), (255, 255, 200), "Neon"),
        part("TailLightLeft", (1.2, 0.6, 0.2), (-W / 2 + 1, 3.8, -L / 2 + 0.4), (255, 0, 0), "Neon"),
        part("TailLightRight", (1.2, 0.6, 0.2), (W / 2 - 1, 3.8, -L / 2 + 0.4), (255, 0, 0), "Neon"),
    ]
    for nm, x, z in (("WheelFL", -W / 2 - 0.4, 4.2), ("WheelFR", W / 2 + 0.4, 4.2),
                     ("WheelRL", -W / 2 - 0.4, -4.2), ("WheelRR", W / 2 + 0.4, -4.2)):
        kids.append(part(nm, (1.2, 3, 3), (x, 1.5, z), (25, 25, 25), "SmoothPlastic", shape="Cylinder"))
    tree = node("Model", "GodmodeCar", {}, kids)
    return ToolOutput(
        summary="A part-based car body (chassis, body, cabin, glass, neon head/tail lights, 4 cylinder wheels). "
                "It is a static, anchored prop - not drivable (no constraints or seat).",
        actions=[create_instances("Workspace", tree, "Build car body")])
