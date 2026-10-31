"""R15 rig description: pose hierarchy, Motor6D names and joint-angle limits.

Axis convention (joint / Motor6D space, matching Roblox CFrame.Angles):
+X right, +Y up, -Z forward. Rotations are degrees, applied X then Y then Z.

* Shoulder / hip  +X  swings the limb forward (and up).
* Elbow           +X  flexes the forearm (0 = straight; it cannot hyper-extend).
* Knee            -X  bends the knee (0 = straight).
* Waist / neck    -X  leans / nods forward, +X leans back / looks up.
* Ankle           +X  toes up (dorsiflexion), -X toes down (plantarflexion).
* Right arm       +Z  raises it out to the side; left arm uses -Z (mirror).
* Right leg       +Z  abducts outward; left leg uses -Z.

The limits below are conservative, human-plausible ranges chosen by hand for
procedural animation (not anatomical data). The root (LowerTorso, Motor6D
"Root") is allowed large rotations so a body can fall to the ground.
"""

from __future__ import annotations

# part -> parent part (KeyframeSequence Pose nesting). HumanoidRootPart is the root pose.
HIERARCHY: dict[str, str | None] = {
    "HumanoidRootPart": None,
    "LowerTorso": "HumanoidRootPart",
    "UpperTorso": "LowerTorso",
    "Head": "UpperTorso",
    "LeftUpperArm": "UpperTorso",
    "LeftLowerArm": "LeftUpperArm",
    "LeftHand": "LeftLowerArm",
    "RightUpperArm": "UpperTorso",
    "RightLowerArm": "RightUpperArm",
    "RightHand": "RightLowerArm",
    "LeftUpperLeg": "LowerTorso",
    "LeftLowerLeg": "LeftUpperLeg",
    "LeftFoot": "LeftLowerLeg",
    "RightUpperLeg": "LowerTorso",
    "RightLowerLeg": "RightUpperLeg",
    "RightFoot": "RightLowerLeg",
}

# part -> Motor6D name (the joint that drives the part)
JOINTS: dict[str, str] = {
    "LowerTorso": "Root", "UpperTorso": "Waist", "Head": "Neck",
    "LeftUpperArm": "LeftShoulder", "LeftLowerArm": "LeftElbow", "LeftHand": "LeftWrist",
    "RightUpperArm": "RightShoulder", "RightLowerArm": "RightElbow", "RightHand": "RightWrist",
    "LeftUpperLeg": "LeftHip", "LeftLowerLeg": "LeftKnee", "LeftFoot": "LeftAnkle",
    "RightUpperLeg": "RightHip", "RightLowerLeg": "RightKnee", "RightFoot": "RightAnkle",
}

POSED_PARTS: tuple[str, ...] = tuple(JOINTS)

Range = tuple[float, float]

# part -> ((xmin,xmax),(ymin,ymax),(zmin,zmax)) rotation limits in degrees
ROT_LIMITS: dict[str, tuple[Range, Range, Range]] = {
    "LowerTorso": ((-95, 95), (-180, 180), (-95, 95)),
    "UpperTorso": ((-45, 30), (-60, 60), (-35, 35)),
    "Head": ((-50, 40), (-75, 75), (-35, 35)),
    "RightUpperArm": ((-60, 180), (-90, 90), (-30, 180)),
    "LeftUpperArm": ((-60, 180), (-90, 90), (-180, 30)),
    "RightLowerArm": ((0, 150), (-20, 20), (-20, 20)),
    "LeftLowerArm": ((0, 150), (-20, 20), (-20, 20)),
    "RightHand": ((-70, 70), (-30, 30), (-45, 45)),
    "LeftHand": ((-70, 70), (-30, 30), (-45, 45)),
    "RightUpperLeg": ((-45, 120), (-45, 45), (-20, 60)),
    "LeftUpperLeg": ((-45, 120), (-45, 45), (-60, 20)),
    "RightLowerLeg": ((-150, 0), (-5, 5), (-5, 5)),
    "LeftLowerLeg": ((-150, 0), (-5, 5), (-5, 5)),
    "RightFoot": ((-50, 35), (-20, 20), (-20, 20)),
    "LeftFoot": ((-50, 35), (-20, 20), (-20, 20)),
}

# part -> position offset limits in studs. Only the root may translate far (bob, crouch, fall).
_SMALL: tuple[Range, Range, Range] = ((-0.5, 0.5), (-0.5, 0.5), (-0.5, 0.5))
POS_LIMITS: dict[str, tuple[Range, Range, Range]] = {p: _SMALL for p in POSED_PARTS}
POS_LIMITS["LowerTorso"] = ((-4.0, 4.0), (-3.0, 2.0), (-4.0, 4.0))

MIRROR: dict[str, str] = {}
for _p in POSED_PARTS:
    if _p.startswith("Left"):
        MIRROR[_p] = "Right" + _p[4:]
    elif _p.startswith("Right"):
        MIRROR[_p] = "Left" + _p[5:]
    else:
        MIRROR[_p] = _p


def limits_table_markdown() -> str:
    rows = ["| part (joint) | X deg | Y deg | Z deg |", "|---|---|---|---|"]
    for p in POSED_PARTS:
        (x0, x1), (y0, y1), (z0, z1) = ROT_LIMITS[p]
        rows.append(f"| {p} ({JOINTS[p]}) | {x0}..{x1} | {y0}..{y1} | {z0}..{z1} |")
    return "\n".join(rows)
