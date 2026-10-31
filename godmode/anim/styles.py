"""Hand-keyed procedural R15 animation styles.

These were designed from the *titles* of reference videos the user supplied
(e.g. a "Fugitive"-style chase run, a Squid-Game-inspired elimination fall, an
"aw shucks" emote). We cannot watch video; nothing here is motion capture.
Poses follow classic animation principles: anticipation before big actions,
overshoot / follow-through after them, and eased (Cubic) in-betweens.

Times are seconds at speed 1.0; angles are degrees at intensity 1.0. See
rig.py for the axis convention and joint limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rig import MIRROR

ALIAS = {
    "root": "LowerTorso", "waist": "UpperTorso", "neck": "Head",
    "lsh": "LeftUpperArm", "lel": "LeftLowerArm", "lwr": "LeftHand",
    "rsh": "RightUpperArm", "rel": "RightLowerArm", "rwr": "RightHand",
    "lhip": "LeftUpperLeg", "lknee": "LeftLowerLeg", "lank": "LeftFoot",
    "rhip": "RightUpperLeg", "rknee": "RightLowerLeg", "rank": "RightFoot",
}

Pose = dict  # part -> {"rotation": [x,y,z], "position": [x,y,z]}


def P(rootp: tuple[float, float, float] | None = None, **joints) -> Pose:
    pose: Pose = {}
    for alias, rot in joints.items():
        pose[ALIAS[alias]] = {"rotation": list(map(float, rot)), "position": [0.0, 0.0, 0.0]}
    if rootp is not None:
        pose.setdefault("LowerTorso", {"rotation": [0.0, 0.0, 0.0], "position": [0.0, 0.0, 0.0]})
        pose["LowerTorso"]["position"] = list(map(float, rootp))
    return pose


def mirror(pose: Pose) -> Pose:
    """Left/right mirror: swap sides, negate Y/Z rotations and X translation."""
    out: Pose = {}
    for part_name, v in pose.items():
        rx, ry, rz = v["rotation"]
        px, py, pz = v["position"]
        out[MIRROR[part_name]] = {"rotation": [rx, -ry, -rz], "position": [-px, py, pz]}
    return out


def merge(*poses: Pose) -> Pose:
    out: Pose = {}
    for p in poses:
        for k, v in p.items():
            out[k] = {"rotation": list(v["rotation"]), "position": list(v["position"])}
    return out


@dataclass
class Key:
    time: float
    name: str
    pose: Pose
    easing_style: str = "Cubic"
    easing_direction: str = "InOut"


@dataclass
class Style:
    name: str
    description: str
    duration: float
    looped: bool
    priority: str
    keys: list[Key]
    markers: list[dict] = field(default_factory=list)
    keywords: tuple[str, ...] = ()


NEUTRAL = P(lsh=(0, 0, -4), rsh=(0, 0, 4), lel=(6, 0, 0), rel=(6, 0, 0))
GUARD = P(rootp=(0, -0.2, 0), root=(0, 25, 0), waist=(-5, -20, 0), neck=(0, -10, 0),
          lsh=(65, 0, -12), lel=(120, 0, 0), rsh=(50, 0, 15), rel=(135, 0, 0),
          lhip=(18, 0, -6), lknee=(-18, 0, 0), rhip=(-12, 0, 6), rknee=(-22, 0, 0), rank=(-8, 0, 0))


def _idle() -> Style:
    a = NEUTRAL
    b = merge(NEUTRAL, P(rootp=(0, 0.05, 0), waist=(3, 0, 0), neck=(-2, 0, 0), lsh=(0, 0, -7), rsh=(0, 0, 7),
                         lel=(9, 0, 0), rel=(9, 0, 0)))
    return Style("idle", "Breathing idle: chest rises, shoulders lift slightly, head settles.", 2.4, True, "Idle",
                 [Key(0, "exhale", a), Key(1.2, "inhale", b), Key(2.4, "exhale", a)],
                 keywords=("idle", "breathing", "breathe", "standing"))


def _walk() -> Style:
    contact = P(rootp=(0, -0.05, 0), root=(0, -5, 0), waist=(0, 8, 0),
                lhip=(25, 0, 0), lknee=(-5, 0, 0), lank=(10, 0, 0),
                rhip=(-20, 0, 0), rknee=(-15, 0, 0), rank=(-12, 0, 0),
                lsh=(-20, 0, -4), lel=(12, 0, 0), rsh=(22, 0, 4), rel=(20, 0, 0))
    passing = P(rootp=(0, 0.12, 0), lhip=(-4, 0, 0), lknee=(-6, 0, 0), rhip=(12, 0, 0), rknee=(-42, 0, 0),
                rank=(5, 0, 0), lsh=(0, 0, -4), lel=(10, 0, 0), rsh=(0, 0, 4), rel=(10, 0, 0))
    return Style("walk", "Four-pose walk cycle (contact/passing, mirrored) with counter-rotating torso and arm swing.",
                 1.0, True, "Movement",
                 [Key(0, "contact_L", contact), Key(0.25, "passing_R", passing), Key(0.5, "contact_R", mirror(contact)),
                  Key(0.75, "passing_L", mirror(passing)), Key(1.0, "contact_L", contact)],
                 keywords=("walk", "walking", "stroll"))


def _run() -> Style:
    contact = P(rootp=(0, -0.25, 0), root=(-16, -8, 0), waist=(-10, 12, 0), neck=(14, -6, 0),
                lhip=(55, 0, 0), lknee=(-25, 0, 0), lank=(12, 0, 0),
                rhip=(-35, 0, 0), rknee=(-70, 0, 0), rank=(-25, 0, 0),
                lsh=(-55, 0, -8), lel=(85, 0, 0), lwr=(0, 0, 0), rsh=(70, 0, 8), rel=(100, 0, 0))
    flight = P(rootp=(0, 0.35, 0), root=(-18, 0, 0), waist=(-12, 0, 0), neck=(16, 0, 0),
               lhip=(15, 0, 0), lknee=(-60, 0, 0), lank=(-15, 0, 0),
               rhip=(40, 0, 0), rknee=(-110, 0, 0), rank=(5, 0, 0),
               lsh=(10, 0, -8), lel=(95, 0, 0), rsh=(10, 0, 8), rel=(95, 0, 0))
    return Style("run", "Sprint / chase run ('Fugitive'-style): strong forward lean, big arm pump with ~90 deg elbows, "
                 "high knee drive and a flight phase.", 0.6, True, "Movement",
                 [Key(0, "contact_L", contact), Key(0.15, "flight", flight), Key(0.3, "contact_R", mirror(contact)),
                  Key(0.45, "flight", mirror(flight)), Key(0.6, "contact_L", contact)],
                 keywords=("run", "running", "sprint", "sprinting", "chase", "fugitive", "flee", "dash"))


def _jump() -> Style:
    crouch = P(rootp=(0, -0.8, 0), root=(-10, 0, 0), waist=(-15, 0, 0), neck=(10, 0, 0),
               lhip=(55, 0, 0), rhip=(55, 0, 0), lknee=(-85, 0, 0), rknee=(-85, 0, 0), lank=(25, 0, 0), rank=(25, 0, 0),
               lsh=(-45, 0, -10), rsh=(-45, 0, 10), lel=(20, 0, 0), rel=(20, 0, 0))
    takeoff = P(rootp=(0, 0.3, 0), waist=(5, 0, 0), neck=(10, 0, 0), lhip=(-5, 0, 0), rhip=(-5, 0, 0),
                lank=(-35, 0, 0), rank=(-35, 0, 0), lsh=(150, 0, -15), rsh=(150, 0, 15), lel=(15, 0, 0), rel=(15, 0, 0))
    apex = P(rootp=(0, 0.4, 0), waist=(-5, 0, 0), lhip=(60, 0, 0), rhip=(50, 0, 0), lknee=(-75, 0, 0),
             rknee=(-60, 0, 0), lsh=(120, 0, -25), rsh=(120, 0, 25), lel=(30, 0, 0), rel=(30, 0, 0))
    land = P(rootp=(0, -0.7, 0), root=(-8, 0, 0), waist=(-18, 0, 0), neck=(8, 0, 0), lhip=(48, 0, 0), rhip=(48, 0, 0),
             lknee=(-75, 0, 0), rknee=(-75, 0, 0), lank=(22, 0, 0), rank=(22, 0, 0),
             lsh=(30, 0, -25), rsh=(30, 0, 25), lel=(30, 0, 0), rel=(30, 0, 0))
    return Style("jump", "Jump with anticipation crouch, extended take-off, tucked apex and an absorbing landing.",
                 1.1, False, "Action",
                 [Key(0, "start", NEUTRAL), Key(0.22, "anticipation", crouch, "Cubic", "Out"),
                  Key(0.36, "takeoff", takeoff, "Cubic", "In"), Key(0.62, "apex", apex, "Cubic", "Out"),
                  Key(0.84, "land", land, "Cubic", "In"), Key(1.1, "recover", NEUTRAL, "Cubic", "Out")],
                 markers=[{"time": 0.36, "name": "TakeOff", "value": ""}, {"time": 0.84, "name": "Land", "value": ""}],
                 keywords=("jump", "jumping", "leap", "hop"))


def _punch() -> Style:
    g = GUARD
    jab_wind = merge(g, P(waist=(-5, -28, 0), lsh=(50, 0, -12), lel=(130, 0, 0)))
    jab = merge(g, P(rootp=(0, -0.2, -0.15), waist=(-8, 5, 0), lsh=(90, 0, -5), lel=(8, 0, 0), lwr=(0, 0, 0)))
    cross_wind = merge(g, P(root=(0, 35, 0), waist=(-5, -30, 0), rsh=(40, 0, 18), rel=(145, 0, 0)))
    cross = merge(g, P(rootp=(0, -0.25, -0.3), root=(0, -10, 0), waist=(-10, 25, 0), rsh=(90, 0, 5), rel=(6, 0, 0),
                       rhip=(-15, 20, 6), rknee=(-15, 0, 0), rank=(-25, 0, 0)))
    hook_wind = merge(g, P(root=(0, -15, 0), waist=(-5, 30, 0), lsh=(70, 0, -55), lel=(95, 0, 0)))
    hook = merge(g, P(rootp=(0, -0.25, 0), root=(0, 30, 0), waist=(-8, -40, 0), lsh=(85, 0, -70), lel=(90, 0, 0),
                      lhip=(15, -20, -6)))
    follow = merge(hook, P(root=(0, 38, 0), waist=(-10, -52, 0), lsh=(80, 0, -60), lel=(95, 0, 0)))
    return Style("punch", "Three-hit combo: jab, cross, hook. Each hit has a wind-up and the hook overshoots "
                 "(follow-through) before returning to guard.", 1.6, False, "Action",
                 [Key(0, "neutral", NEUTRAL), Key(0.12, "guard", g), Key(0.2, "jab_windup", jab_wind),
                  Key(0.28, "jab", jab, "Cubic", "Out"), Key(0.42, "guard", g),
                  Key(0.52, "cross_windup", cross_wind), Key(0.62, "cross", cross, "Cubic", "Out"), Key(0.78, "guard", g),
                  Key(0.9, "hook_windup", hook_wind), Key(1.02, "hook", hook, "Cubic", "Out"),
                  Key(1.14, "follow_through", follow, "Cubic", "Out"), Key(1.36, "guard", g), Key(1.6, "neutral", NEUTRAL)],
                 markers=[{"time": 0.28, "name": "Hit", "value": "jab"}, {"time": 0.62, "name": "Hit", "value": "cross"},
                          {"time": 1.02, "name": "Hit", "value": "hook"}],
                 keywords=("punch", "punching", "combo", "jab", "boxing", "uppercut"))


def _kick() -> Style:
    chamber = merge(GUARD, P(rootp=(0, -0.1, 0), waist=(15, -10, 0), rhip=(85, 0, 10), rknee=(-115, 0, 0), rank=(-20, 0, 0),
                             lknee=(-25, 0, 0)))
    extend = merge(GUARD, P(waist=(22, -10, 0), neck=(-10, -10, 0), rhip=(90, 0, 10), rknee=(-5, 0, 0), rank=(-35, 0, 0),
                            lknee=(-20, 0, 0), lank=(5, 0, 0), lsh=(40, 0, -30), rsh=(-20, 0, 35)))
    return Style("kick", "Front kick: guard, chamber the knee (anticipation), snap the leg out, re-chamber, plant.",
                 1.2, False, "Action",
                 [Key(0, "neutral", NEUTRAL), Key(0.15, "guard", GUARD), Key(0.35, "chamber", chamber, "Cubic", "Out"),
                  Key(0.47, "kick", extend, "Cubic", "Out"), Key(0.66, "rechamber", chamber),
                  Key(0.88, "plant", GUARD), Key(1.2, "neutral", NEUTRAL)],
                 markers=[{"time": 0.47, "name": "Hit", "value": "kick"}],
                 keywords=("kick", "kicking", "roundhouse"))


def _block() -> Style:
    blk = P(rootp=(0, -0.35, 0), waist=(-12, 0, 0), neck=(-18, 0, 0),
            lsh=(105, 0, 25), lel=(130, 0, 0), rsh=(105, 0, -25), rel=(130, 0, 0),
            lhip=(20, 0, -5), rhip=(20, 0, 5), lknee=(-30, 0, 0), rknee=(-30, 0, 0), lank=(10, 0, 0), rank=(10, 0, 0))
    brace = merge(blk, P(rootp=(0, -0.42, 0.05), waist=(-15, 0, 0), lel=(135, 0, 0), rel=(135, 0, 0)))
    return Style("block", "Looping guard/block hold: forearms up covering the face, knees bent, subtle bracing pulse. "
                 "Play while the block input is held; stop with a fade to release.", 1.0, True, "Action",
                 [Key(0, "block", blk), Key(0.5, "brace", brace), Key(1.0, "block", blk)],
                 keywords=("block", "blocking", "guard", "defend", "shield"))


def _hit() -> Style:
    impact = P(rootp=(0, 0, 0.35), root=(8, 0, 6), waist=(25, 10, 0), neck=(30, 0, 10),
               lsh=(25, 0, -40), rsh=(20, 0, 45), lel=(30, 0, 0), rel=(25, 0, 0))
    stagger = P(rootp=(0, -0.25, 0.6), root=(6, 5, 8), waist=(12, 5, 0), neck=(10, 0, 8), lsh=(10, 0, -25), rsh=(15, 0, 25),
                lel=(20, 0, 0), rel=(20, 0, 0), rhip=(-15, 0, 5), rknee=(-20, 0, 0), lhip=(15, 0, 0), lknee=(-35, 0, 0))
    recover = P(rootp=(0, -0.05, 0.2), waist=(-8, 0, 0), neck=(-6, 0, 0), lsh=(0, 0, -8), rsh=(0, 0, 8),
                lel=(10, 0, 0), rel=(10, 0, 0))
    return Style("hit", "Hit reaction / stagger: head and chest snap back on impact, a stumbling step back, an "
                 "overshoot forward (follow-through) and recovery.", 0.9, False, "Action",
                 [Key(0, "neutral", NEUTRAL), Key(0.08, "impact", impact, "Cubic", "Out"),
                  Key(0.32, "stagger", stagger), Key(0.6, "recover", recover), Key(0.9, "neutral", NEUTRAL)],
                 keywords=("hit", "hurt", "stagger", "flinch", "damage", "damaged", "reaction", "stumble"))


def _knockdown() -> Style:
    jolt = P(waist=(15, 0, 0), neck=(20, 0, 0), lsh=(20, 0, -25), rsh=(20, 0, 25), lel=(20, 0, 0), rel=(20, 0, 0))
    buckle = P(rootp=(0, -1.0, 0), root=(-10, 0, 0), waist=(-20, 0, 0), neck=(-25, 0, 0),
               lhip=(40, 0, 0), rhip=(35, 0, 0), lknee=(-75, 0, 0), rknee=(-65, 0, 0), lank=(20, 0, 0), rank=(20, 0, 0),
               lsh=(10, 0, -8), rsh=(10, 0, 8), lel=(15, 0, 0), rel=(15, 0, 0))
    falling = P(rootp=(0, -1.7, -0.7), root=(-50, 0, 5), waist=(-15, 0, 0), neck=(15, 0, 0),
                lhip=(30, 0, 0), rhip=(25, 0, 0), lknee=(-45, 0, 0), rknee=(-40, 0, 0),
                lsh=(60, 0, -15), rsh=(60, 0, 15), lel=(25, 0, 0), rel=(25, 0, 0))
    impact = P(rootp=(0, -2.5, -1.3), root=(-86, 0, 5), waist=(-5, 0, 0), neck=(10, 35, 0),
               lhip=(5, 0, -5), rhip=(0, 0, 8), lknee=(-10, 0, 0), rknee=(-25, 0, 0), lank=(-30, 0, 0), rank=(-30, 0, 0),
               lsh=(150, 0, -20), rsh=(30, 0, 20), lel=(20, 0, 0), rel=(15, 0, 0))
    bounce = merge(impact, P(rootp=(0, -2.3, -1.3), root=(-82, 0, 5), waist=(-8, 0, 0), neck=(14, 35, 0),
                             lsh=(140, 0, -24), rsh=(35, 0, 24), lknee=(-15, 0, 0), rknee=(-30, 0, 0),
                             lank=(-30, 0, 0), rank=(-30, 0, 0), lel=(25, 0, 0), rel=(15, 0, 0), lhip=(5, 0, -5),
                             rhip=(0, 0, 8)))
    settle = merge(impact, P(rootp=(0, -2.55, -1.35), root=(-88, 0, 5), waist=(-5, 0, 0), neck=(8, 38, 0),
                             lsh=(150, 0, -20), rsh=(30, 0, 20), lknee=(-8, 0, 0), rknee=(-22, 0, 0),
                             lank=(-30, 0, 0), rank=(-30, 0, 0), lel=(20, 0, 0), rel=(15, 0, 0), lhip=(5, 0, -5),
                             rhip=(0, 0, 8)))
    return Style("knockdown", "Knockdown / 'elimination' collapse (Squid-Game-inspired, non-gory): a jolt, knees buckle, "
                 "the body pitches forward and hits the ground with a small bounce, then settles face-down. "
                 "Ends lying down (not looped).", 2.0, False, "Action4",
                 [Key(0, "neutral", NEUTRAL), Key(0.1, "jolt", jolt, "Cubic", "Out"), Key(0.45, "buckle", buckle),
                  Key(0.8, "falling", falling, "Cubic", "In"), Key(1.1, "impact", impact, "Cubic", "In"),
                  Key(1.28, "bounce", bounce, "Cubic", "Out"), Key(2.0, "settle", settle, "Bounce", "Out")],
                 markers=[{"time": 1.1, "name": "GroundImpact", "value": ""}],
                 keywords=("knockdown", "knocked", "fall", "falls", "falling", "collapse", "eliminated", "elimination",
                           "squid", "ragdoll", "death", "die", "dies", "faint"))


def _victory() -> Style:
    crouch = P(rootp=(0, -0.5, 0), waist=(-12, 0, 0), neck=(-10, 0, 0), lhip=(30, 0, 0), rhip=(30, 0, 0),
               lknee=(-45, 0, 0), rknee=(-45, 0, 0), lank=(15, 0, 0), rank=(15, 0, 0),
               lsh=(-30, 0, -10), rsh=(-30, 0, 10), lel=(40, 0, 0), rel=(40, 0, 0))
    up = P(rootp=(0, 0.3, 0), waist=(15, 0, 0), neck=(25, 0, 0), lsh=(0, 0, -165), rsh=(0, 0, 165),
           lel=(20, 0, 0), rel=(20, 0, 0), lank=(-20, 0, 0), rank=(-20, 0, 0))
    pump = merge(up, P(rootp=(0, 0.1, 0), waist=(10, 0, 0), neck=(20, 0, 0), lsh=(0, 0, -145), rsh=(0, 0, 145),
                       lel=(60, 0, 0), rel=(60, 0, 0)))
    return Style("victory", "Victory pose: anticipation crouch, explosive two-fist raise, two fist pumps, hold.",
                 2.0, False, "Action",
                 [Key(0, "neutral", NEUTRAL), Key(0.3, "anticipation", crouch, "Cubic", "Out"),
                  Key(0.6, "fists_up", up, "Cubic", "Out"), Key(0.85, "pump", pump), Key(1.1, "fists_up", up),
                  Key(1.35, "pump", pump), Key(1.6, "fists_up", up), Key(2.0, "hold", up)],
                 keywords=("victory", "win", "winner", "celebrate", "celebration", "triumph", "cheer"))


def _wave() -> Style:
    a = P(waist=(0, 0, -4), neck=(5, 10, 0), rsh=(10, 0, 140), rel=(35, 0, 0), rwr=(0, 0, -25), lsh=(0, 0, -4), lel=(8, 0, 0))
    b = merge(a, P(rsh=(10, 0, 160), rel=(25, 0, 0), rwr=(0, 0, 25)))
    return Style("wave", "Friendly looping wave: right arm raised, forearm and hand swinging, head tilted toward the "
                 "viewer.", 1.2, True, "Action",
                 [Key(0, "wave_in", a), Key(0.3, "wave_out", b), Key(0.6, "wave_in", a), Key(0.9, "wave_out", b),
                  Key(1.2, "wave_in", a)],
                 keywords=("wave", "waving", "hello", "greet", "greeting", "bye"))


def _shucks() -> Style:
    shrug = P(rootp=(0, 0.12, 0), waist=(-5, 0, 0), neck=(-12, 0, 0), lsh=(5, 0, -14), rsh=(5, 0, 14),
              lel=(20, 0, 0), rel=(20, 0, 0))
    reach = P(rootp=(0, 0.05, 0), waist=(-10, 5, -4), neck=(-30, -10, 15), rsh=(125, 0, 55), rel=(140, 0, 0),
              rwr=(10, 0, 10), lsh=(5, 0, -10), lel=(25, 0, 0), rhip=(8, 15, 0), rknee=(-12, 0, 0))
    rub1 = merge(reach, P(rel=(130, 0, 0), rwr=(10, 0, 25)))
    rub2 = merge(reach, P(rel=(145, 0, 0), rwr=(10, 0, -20)))
    scuff_a = merge(rub1, P(rhip=(18, -15, 0), rknee=(-20, 0, 0), rank=(-25, 0, 0)))
    scuff_b = merge(rub2, P(rhip=(18, 18, 0), rknee=(-22, 0, 0), rank=(-25, 0, 0), neck=(-35, -12, 18)))
    shrug2 = merge(shrug, P(neck=(-22, 0, 10), lsh=(5, 0, -16), rsh=(5, 0, 16)))
    return Style("shucks", "'Aw shucks' sheepish emote: shoulders shrug, head tilts down, right hand rubs the back of "
                 "the neck while the right foot scuffs the ground, a final shrug and back to neutral.", 2.4, False, "Action",
                 [Key(0, "neutral", NEUTRAL), Key(0.3, "shrug", shrug, "Cubic", "Out"), Key(0.7, "reach_neck", reach),
                  Key(0.95, "rub", rub1), Key(1.2, "rub", rub2), Key(1.45, "scuff", scuff_a), Key(1.7, "scuff", scuff_b),
                  Key(2.05, "shrug", shrug2), Key(2.4, "neutral", NEUTRAL)],
                 keywords=("shucks", "aw shucks", "sheepish", "embarrassed", "shy", "bashful", "awkward"))


def _dance() -> Style:
    beat1 = P(rootp=(-0.3, -0.25, 0), root=(0, 0, 8), waist=(0, 10, -8), neck=(0, -10, 6), lsh=(20, 0, -40), rsh=(-20, 0, 25),
              lel=(80, 0, 0), rel=(60, 0, 0), lhip=(10, 0, -8), rhip=(-5, 0, 4), lknee=(-25, 0, 0), rknee=(-15, 0, 0))
    up1 = P(rootp=(0, 0.15, 0), waist=(5, 0, 0), neck=(10, 0, 0), lsh=(0, 0, -150), rsh=(30, 0, 10), lel=(20, 0, 0),
            rel=(90, 0, 0))
    return Style("dance", "Looping 4-beat groove: hip sway with knee bounce on each beat, alternating arm raises and "
                 "counter-tilted head.", 2.0, True, "Action",
                 [Key(0, "beat1", beat1), Key(0.5, "up", up1), Key(1.0, "beat3", mirror(beat1)), Key(1.5, "up", mirror(up1)),
                  Key(2.0, "beat1", beat1)],
                 keywords=("dance", "dancing", "groove", "boogie", "party"))


def _sit() -> Style:
    seat = P(rootp=(0, -1.0, 0), waist=(-4, 0, 0), lhip=(90, 0, -4), rhip=(90, 0, 4), lknee=(-90, 0, 0),
             rknee=(-90, 0, 0), lsh=(30, 0, -6), rsh=(30, 0, 6), lel=(40, 0, 0), rel=(40, 0, 0))
    breathe = merge(seat, P(rootp=(0, -0.97, 0), waist=(-1, 0, 0), neck=(-3, 0, 0), lhip=(90, 0, -4), rhip=(90, 0, 4)))
    return Style("sit", "Seated loop: hips and knees at 90 deg, hands resting on the lap, slow breathing.", 2.4, True,
                 "Idle", [Key(0, "seated", seat), Key(1.2, "breathe", breathe), Key(2.4, "seated", seat)],
                 keywords=("sit", "sitting", "seated", "chair"))


def _crouch() -> Style:
    step = P(rootp=(0, -1.0, 0), root=(-20, -5, 0), waist=(-15, 6, 0), neck=(18, -4, 0),
             lhip=(70, 0, 0), lknee=(-95, 0, 0), lank=(15, 0, 0), rhip=(30, 0, 0), rknee=(-110, 0, 0), rank=(-10, 0, 0),
             lsh=(15, 0, -12), rsh=(40, 0, 12), lel=(70, 0, 0), rel=(75, 0, 0))
    mid = P(rootp=(0, -0.9, 0), root=(-20, 0, 0), waist=(-15, 0, 0), neck=(18, 0, 0),
            lhip=(45, 0, 0), lknee=(-100, 0, 0), rhip=(60, 0, 0), rknee=(-120, 0, 0), rank=(10, 0, 0),
            lsh=(28, 0, -12), rsh=(28, 0, 12), lel=(72, 0, 0), rel=(72, 0, 0))
    return Style("crouch", "Crouch-sneak loop: low centre of mass, torso pitched forward, head up scanning, slow "
                 "careful steps with arms ready.", 1.6, True, "Movement",
                 [Key(0, "step_L", step), Key(0.4, "pass", mid), Key(0.8, "step_R", mirror(step)),
                  Key(1.2, "pass", mirror(mid)), Key(1.6, "step_L", step)],
                 keywords=("crouch", "crouching", "sneak", "sneaking", "stealth", "creep", "tiptoe"))


STYLES: dict[str, Style] = {s.name: s for s in (
    _idle(), _walk(), _run(), _jump(), _punch(), _kick(), _block(), _hit(), _knockdown(), _victory(), _wave(),
    _shucks(), _dance(), _sit(), _crouch())}
