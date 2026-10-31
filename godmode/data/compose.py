"""Compositional, multilingual (English / French / Spanish) request -> script data.

Why this exists: the family generators in ``script_families.py`` teach the model ~35 fixed
kinds of script, and the model only learned to recall those (held-out *families* stayed
at 11-22% complete). Here a request is built from independent parts:

    trigger (touch / click / proximity prompt / spawn / timer / chat command)
  x 1-2 effects (kill, damage N, heal, speed, jump power, stat +N, teleport, launch,
                 forcefield, fire/sparkles/smoke, scale, explode, give tool, sit,
                 max health, recolour part, hide part)
  x parameters (numbers, names, colours, cooldown / once-per-player)

and the script is assembled from the code for each part. The request is phrased in
English, French or Spanish with randomised wording. Whole (trigger, effect) PAIRS are held
out of training (``HELD_OUT_PAIRS``): at test time the model must combine pieces it has
only seen separately, in any of the three languages, and the grader checks that the code
contains the right event, the right effect API calls *and the exact numbers asked for*.

Honesty note: the code pieces are written by a person; the model is trained to compose them
from the request. Words outside this vocabulary, and languages other than EN/FR/ES, are not
trained (the byte-level tokenizer accepts any text, but the model will not understand it).
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass, field

from ..luau import check_structure, lint

LANGS = ("en", "fr", "es")
TRIGGERS = ("touch", "click", "prompt", "spawn", "timer", "chat")
PART_TRIGGERS = ("touch", "click", "prompt")

# (trigger, effect) pairs never present in training data, in any language.
HELD_OUT_PAIRS = frozenset({("click", "speed"), ("prompt", "teleport"), ("touch", "forcefield"), ("spawn", "jump"),
                            ("timer", "heal"), ("chat", "launch"), ("click", "fx"), ("prompt", "scale")})

#            code name, rgb,            en,       fr (m, f),            es (m, f)
COLORS = [("red", (255, 0, 0), "red", ("rouge", "rouge"), ("rojo", "roja")),
          ("green", (0, 200, 70), "green", ("vert", "verte"), ("verde", "verde")),
          ("blue", (0, 120, 255), "blue", ("bleu", "bleue"), ("azul", "azul")),
          ("yellow", (255, 210, 0), "yellow", ("jaune", "jaune"), ("amarillo", "amarilla")),
          ("purple", (150, 60, 220), "purple", ("violet", "violette"), ("morado", "morada")),
          ("black", (20, 20, 20), "black", ("noir", "noire"), ("negro", "negra")),
          ("white", (240, 240, 240), "white", ("blanc", "blanche"), ("blanco", "blanca")),
          ("orange", (255, 140, 0), "orange", ("orange", "orange"), ("naranja", "naranja"))]
DEFAULT_RGB = (163, 162, 165)

STATS = {"en": ["Coins", "Gems", "Points", "Cash", "Gold", "Wins"],
         "fr": ["Coins", "Gems", "Points", "Pièces", "Gemmes", "Or", "Victoires"],
         "es": ["Coins", "Gems", "Points", "Monedas", "Gemas", "Oro", "Victorias"]}
TOOL_NAMES = ["Sword", "Katana", "Bat", "Flashlight", "Hammer", "Bow"]
PART_NAMES = ["Pad", "Zone", "Pedestal", "Altar", "Crystal", "Portal", "Station", "Orb"]

# Objects the request can be about (part triggers): (noun with indefinite article, with definite article, gender)
OBJECTS = {
    "touch": {"en": [("a part", "the part"), ("a brick", "the brick"), ("a pad", "the pad"), ("a block", "the block"),
                     ("a floor tile", "the tile")],
              "fr": [("une brique", "la brique", "f"), ("un bloc", "le bloc", "m"), ("une plaque", "la plaque", "f"),
                     ("une part", "la part", "f"), ("une dalle", "la dalle", "f")],
              "es": [("un bloque", "el bloque", "m"), ("una parte", "la parte", "f"), ("una plataforma", "la plataforma", "f"),
                     ("un ladrillo", "el ladrillo", "m"), ("una baldosa", "la baldosa", "f")]},
    "click": {"en": [("a button", "the button"), ("a part you can click", "the part"), ("a clickable block", "the block")],
              "fr": [("un bouton", "le bouton", "m"), ("une part cliquable", "la part", "f"), ("un bloc cliquable", "le bloc", "m")],
              "es": [("un botón", "el botón", "m"), ("una parte clicable", "la parte", "f"), ("un bloque clicable", "el bloque", "m")]},
    "prompt": {"en": [("a part with a proximity prompt", "the prompt"), ("a proximity prompt", "the prompt"),
                      ("an E prompt", "the prompt")],
               "fr": [("une part avec un ProximityPrompt", "le prompt", "f"), ("un ProximityPrompt", "le prompt", "m"),
                      ("un bloc avec une touche E", "le bloc", "m")],
               "es": [("una parte con un ProximityPrompt", "el prompt", "f"), ("un ProximityPrompt", "el prompt", "m"),
                      ("un bloque con la tecla E", "el bloque", "m")]},
}


@dataclass
class Spec:
    lang: str
    trigger: str
    effects: list[tuple[str, dict]]
    color: tuple | None = None          # COLORS row, if the request names a colour
    part_name: str | None = None        # if the request names the part
    cooldown: float | None = None       # explicit cooldown in seconds
    once: bool = False
    interval: int | None = None         # timer
    command: str | None = None          # chat

    @property
    def effect_names(self) -> list[str]:
        return [e for e, _ in self.effects]

    def pairs(self) -> set[tuple[str, str]]:
        return {(self.trigger, e) for e in self.effect_names}

    def held_out(self) -> bool:
        return bool(self.pairs() & HELD_OUT_PAIRS)


@dataclass
class ComposedSample:
    spec: Spec
    prompt: str
    solution: str
    checks: list[tuple[str, str]] = field(default_factory=list)  # (description, regex) the answer must satisfy

    @property
    def family(self) -> str:
        return f"compose:{self.spec.trigger}+{'+'.join(self.spec.effect_names)}"


# ----------------------------------------------------------------------------- effects

EFFECTS = ("kill", "damage", "heal", "speed", "jump", "stat", "teleport", "launch", "forcefield", "fx", "scale",
           "explode", "tool", "sit", "maxhealth", "color", "hide")
PART_ONLY = {"color", "hide"}
NEEDS_ROOT = {"teleport", "launch", "fx", "explode"}
ALONE = {"kill", "explode"}           # never combined with a second effect
NOT_ON_SPAWN = {"kill", "damage", "explode", "hide", "color"}


def _params(effect: str, rng: random.Random, lang: str) -> dict:
    r = rng.random
    if effect == "damage":
        return {"n": rng.choice([5, 10, 15, 20, 25, 30, 40, 50])}
    if effect == "heal":
        return {"n": None if r() < 0.4 else rng.choice([10, 20, 25, 30, 50])}
    if effect == "speed":
        return {"n": rng.choice([24, 30, 32, 40, 50, 60, 80, 100]), "d": None if r() < 0.5 else rng.choice([3, 5, 10, 15])}
    if effect == "jump":
        return {"n": rng.choice([70, 80, 100, 120, 150])}
    if effect == "stat":
        return {"stat": rng.choice(STATS[lang]), "n": rng.choice([1, 5, 10, 25, 50, 100])}
    if effect == "teleport":
        if r() < 0.5:
            return {"xyz": (rng.choice([-100, -50, 0, 20, 50, 100]), rng.choice([5, 10, 20, 50]), rng.choice([-80, -30, 0, 30, 80]))}
        return {"target": rng.choice(["SpawnArea", "Lobby", "Arena", "Island", "Checkpoint", "Shop", "Tower"])}
    if effect == "launch":
        return {"n": None if r() < 0.4 else rng.choice([60, 80, 100, 150, 200])}
    if effect == "forcefield":
        return {"d": rng.choice([3, 5, 10, 15, 30])}
    if effect == "fx":
        return {"kind": rng.choice(["Fire", "Sparkles", "Smoke"]), "d": rng.choice([3, 5, 10])}
    if effect == "scale":
        return {"f": rng.choice([0.5, 1.5, 2, 3])}
    if effect == "tool":
        return {"name": rng.choice(TOOL_NAMES)}
    if effect == "maxhealth":
        return {"n": rng.choice([150, 200, 250, 500, 1000])}
    if effect == "color":
        return {"c": None if r() < 0.3 else rng.choice(COLORS)}
    if effect == "hide":
        return {"d": rng.choice([2, 3, 5, 10])}
    return {}


def _num(v) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def effect_code(effect: str, p: dict) -> list[str]:
    """Body lines (no indentation) for one effect. Variables in scope: player, character, humanoid, root, part."""
    if effect == "kill":
        return ["humanoid.Health = 0"]
    if effect == "damage":
        return [f"humanoid:TakeDamage({p['n']})"]
    if effect == "heal":
        if p["n"] is None:
            return ["humanoid.Health = humanoid.MaxHealth"]
        return [f"humanoid.Health = math.min(humanoid.MaxHealth, humanoid.Health + {p['n']})"]
    if effect == "speed":
        out = [f"humanoid.WalkSpeed = {p['n']}"]
        if p["d"]:
            out += [f"task.delay({p['d']}, function()", "\tif humanoid.Parent then", "\t\thumanoid.WalkSpeed = 16",
                    "\tend", "end)"]
        return out
    if effect == "jump":
        return ["humanoid.UseJumpPower = true", f"humanoid.JumpPower = {p['n']}"]
    if effect == "stat":
        return ['local leaderstats = player:FindFirstChild("leaderstats")',
                f'local stat = leaderstats and leaderstats:FindFirstChild("{p["stat"]}")',
                "if stat then", f"\tstat.Value += {p['n']}", "end"]
    if effect == "teleport":
        if "xyz" in p:
            x, y, z = p["xyz"]
            return [f"root.CFrame = CFrame.new({x}, {y}, {z})"]
        return [f'local target = workspace:FindFirstChild("{p["target"]}")', "if target and target:IsA(\"BasePart\") then",
                "\troot.CFrame = target.CFrame + Vector3.new(0, 4, 0)", "end"]
    if effect == "launch":
        return [f"root.AssemblyLinearVelocity = Vector3.new(0, {p['n'] or 100}, 0)"]
    if effect == "forcefield":
        return ['local shield = Instance.new("ForceField")', "shield.Parent = character",
                f"Debris:AddItem(shield, {p['d']})"]
    if effect == "fx":
        return [f'local effect = Instance.new("{p["kind"]}")', "effect.Parent = root", f"Debris:AddItem(effect, {p['d']})"]
    if effect == "scale":
        return [f"character:ScaleTo({_num(p['f'])})"]
    if effect == "explode":
        return ['local explosion = Instance.new("Explosion")', "explosion.Position = root.Position",
                "explosion.Parent = workspace"]
    if effect == "tool":
        return [f'local template = ServerStorage:FindFirstChild("{p["name"]}")',
                "local backpack = player:FindFirstChildOfClass(\"Backpack\")",
                "if template and backpack and not backpack:FindFirstChild(template.Name) then",
                "\ttemplate:Clone().Parent = backpack", "end"]
    if effect == "sit":
        return ["humanoid.Sit = true"]
    if effect == "maxhealth":
        return [f"humanoid.MaxHealth = {p['n']}", f"humanoid.Health = {p['n']}"]
    if effect == "color":
        if p["c"] is None:
            return ["part.Color = Color3.fromRGB(math.random(0, 255), math.random(0, 255), math.random(0, 255))"]
        rgb = p["c"][1]
        return [f"part.Color = Color3.fromRGB({rgb[0]}, {rgb[1]}, {rgb[2]})"]
    if effect == "hide":
        return ["part.Transparency = 1", "part.CanCollide = false", f"task.delay({p['d']}, function()",
                "\tpart.Transparency = 0", "\tpart.CanCollide = true", "end)"]
    raise KeyError(effect)


def effect_checks(effect: str, p: dict) -> list[tuple[str, str]]:
    """Regexes a correct answer must contain (the API used and the exact numbers the request asked for)."""
    n = p.get("n")
    if effect == "kill":
        return [("sets Health to 0", r"Health\s*=\s*0\b")]
    if effect == "damage":
        return [(f"TakeDamage({n})", rf"TakeDamage\(\s*{n}\s*\)")]
    if effect == "heal":
        return [("heals", r"\.Health\s*=\s*(math\.min\(.*MaxHealth|humanoid\.MaxHealth)")] + (
            [(f"heal amount {n}", rf"\+\s*{n}\b")] if n else [])
    if effect == "speed":
        return [(f"WalkSpeed = {n}", rf"WalkSpeed\s*=\s*{n}\b")] + (
            [(f"reverts after {p['d']}s", rf"task\.delay\(\s*{p['d']}\b")] if p["d"] else [])
    if effect == "jump":
        return [(f"JumpPower = {n}", rf"JumpPower\s*=\s*{n}\b")]
    if effect == "stat":
        return [(f"stat {p['stat']}", re.escape(f'"{p["stat"]}"')), (f"+{n}", rf"(\+=\s*{n}\b|\+\s*{n}\b)")]
    if effect == "teleport":
        if "xyz" in p:
            x, y, z = p["xyz"]
            return [("teleports to xyz", rf"CFrame\.new\(\s*{x}\s*,\s*{y}\s*,\s*{z}\s*\)")]
        return [(f"teleports to {p['target']}", re.escape(f'"{p["target"]}"')), ("sets CFrame", r"\.CFrame\s*=")]
    if effect == "launch":
        return [("launches up", r"(AssemblyLinearVelocity|ApplyImpulse|LinearVelocity)")] + (
            [(f"force {n}", rf"\b{n}\b")] if n else [])
    if effect == "forcefield":
        return [("ForceField", r'"ForceField"'), (f"lasts {p['d']}s", rf"\b{p['d']}\b")]
    if effect == "fx":
        return [(p["kind"], rf'"{p["kind"]}"'), (f"lasts {p['d']}s", rf"\b{p['d']}\b")]
    if effect == "scale":
        return [(f"ScaleTo({_num(p['f'])})", rf"ScaleTo\(\s*{re.escape(_num(p['f']))}\s*\)")]
    if effect == "explode":
        return [("Explosion", r'"Explosion"')]
    if effect == "tool":
        return [(f"tool {p['name']}", re.escape(f'"{p["name"]}"')), ("Backpack", r"Backpack")]
    if effect == "sit":
        return [("Sit = true", r"\.Sit\s*=\s*true")]
    if effect == "maxhealth":
        return [(f"MaxHealth = {n}", rf"MaxHealth\s*=\s*{n}\b")]
    if effect == "color":
        if p["c"] is None:
            return [("random colour", r"math\.random")]
        rgb = p["c"][1]
        return [(f"colour {p['c'][0]}", rf"Color3\.fromRGB\(\s*{rgb[0]}\s*,\s*{rgb[1]}\s*,\s*{rgb[2]}\s*\)")]
    if effect == "hide":
        return [("Transparency = 1", r"Transparency\s*=\s*1\b"), (f"returns after {p['d']}s", rf"\b{p['d']}\b")]
    raise KeyError(effect)


TRIGGER_CHECKS = {
    "touch": ("Touched event", r"\.Touched:Connect"),
    "click": ("ClickDetector", r"(MouseClick:Connect|\"ClickDetector\")"),
    "prompt": ("ProximityPrompt", r"(Triggered:Connect|\"ProximityPrompt\")"),
    "spawn": ("CharacterAdded", r"CharacterAdded:Connect"),
    "timer": ("timed loop", r"task\.wait\("),
    "chat": ("Chatted", r"Chatted:Connect"),
}

_DEF_NAME = {"kill": "Kill", "damage": "Damage", "heal": "Heal", "speed": "Speed", "jump": "Jump", "stat": "Reward",
             "teleport": "Teleport", "launch": "Launch", "forcefield": "Shield", "fx": "Effect", "scale": "Size",
             "explode": "Explode", "tool": "Tool", "sit": "Sit", "maxhealth": "Health", "color": "Color", "hide": "Vanish"}
_TRIG_NOUN = {"touch": "Pad", "click": "Button", "prompt": "Prompt"}


def default_part_name(spec: Spec) -> str:
    return _DEF_NAME[spec.effect_names[0]] + _TRIG_NOUN[spec.trigger]


# ----------------------------------------------------------------------------- code assembly

def build_code(spec: Spec) -> str:
    effs = spec.effect_names
    services = ["Players"]
    if any(e in ("forcefield", "fx") for e in effs):
        services.append("Debris")
    if "tool" in effs:
        services.append("ServerStorage")
    title = f"{spec.trigger} -> " + " + ".join(effs)
    out = [f"-- Script in ServerScriptService: {title}"]
    out += [f'local {s} = game:GetService("{s}")' for s in services]
    out.append("")
    if spec.trigger in PART_TRIGGERS:
        name = spec.part_name or default_part_name(spec)
        rgb = spec.color[1] if spec.color else DEFAULT_RGB
        size = "Vector3.new(6, 1, 6)" if spec.trigger == "touch" else "Vector3.new(3, 3, 3)"
        out += [f'local part = workspace:FindFirstChild("{name}")', "if not part then", '\tpart = Instance.new("Part")',
                f'\tpart.Name = "{name}"', f"\tpart.Size = {size}",
                f"\tpart.Position = Vector3.new(0, {'0.5' if spec.trigger == 'touch' else '2'}, 0)",
                "\tpart.Anchored = true", f"\tpart.Color = Color3.fromRGB({rgb[0]}, {rgb[1]}, {rgb[2]})",
                "\tpart.Parent = workspace", "end"]
        if spec.trigger == "click":
            out += ['local detector = part:FindFirstChildOfClass("ClickDetector") or Instance.new("ClickDetector")',
                    "detector.MaxActivationDistance = 32", "detector.Parent = part"]
        elif spec.trigger == "prompt":
            out += ['local prompt = part:FindFirstChildOfClass("ProximityPrompt") or Instance.new("ProximityPrompt")',
                    'prompt.ActionText = "Use"', "prompt.HoldDuration = 0", "prompt.Parent = part"]
        out.append("")
    stat_names = sorted({p["stat"] for e, p in spec.effects if e == "stat"})
    if stat_names:
        out += ["Players.PlayerAdded:Connect(function(player)", "\tlocal leaderstats = player:FindFirstChild(\"leaderstats\")",
                "\tif not leaderstats then", '\t\tleaderstats = Instance.new("Folder")', '\t\tleaderstats.Name = "leaderstats"',
                "\t\tleaderstats.Parent = player", "\tend"]
        for s in stat_names:
            out += [f'\tif not leaderstats:FindFirstChild("{s}") then', '\t\tlocal value = Instance.new("IntValue")',
                    f'\t\tvalue.Name = "{s}"', "\t\tvalue.Parent = leaderstats", "\tend"]
        out += ["end)", ""]
    cooldown = spec.cooldown if spec.cooldown is not None else (1 if spec.trigger == "touch" and not spec.once else None)
    if spec.once:
        out += ["local used = {}", ""]
    elif cooldown is not None:
        out += [f"local COOLDOWN = {_num(cooldown)}", "local lastUsed = {}", ""]
    if spec.once or cooldown is not None:
        table = "used" if spec.once else "lastUsed"
        out += ["Players.PlayerRemoving:Connect(function(player)", f"\t{table}[player] = nil", "end)", ""]

    fn = {"touch": "onTouched", "click": "onClicked", "prompt": "onTriggered", "spawn": "onSpawned", "timer": "applyTo",
          "chat": "onCommand"}[spec.trigger]
    body = [f"local function {fn}(player)", "\tlocal character = player.Character",
            '\tlocal humanoid = character and character:FindFirstChildOfClass("Humanoid")',
            "\tif not humanoid or humanoid.Health <= 0 then", "\t\treturn", "\tend"]
    if spec.once:
        body += ["\tif used[player] then", "\t\treturn", "\tend", "\tused[player] = true"]
    elif cooldown is not None:
        body += ["\tlocal now = os.clock()", "\tif lastUsed[player] and now - lastUsed[player] < COOLDOWN then",
                 "\t\treturn", "\tend", "\tlastUsed[player] = now"]
    if any(e in NEEDS_ROOT for e in effs):
        body += ['\tlocal root = character:FindFirstChild("HumanoidRootPart")', "\tif not root then", "\t\treturn", "\tend"]
    for e, p in spec.effects:
        body += ["\t" + line for line in effect_code(e, p)]
    body += ["end", ""]
    out += body

    if spec.trigger == "touch":
        out += ["part.Touched:Connect(function(hit)", "\tlocal player = Players:GetPlayerFromCharacter(hit.Parent)",
                "\tif player then", f"\t\t{fn}(player)", "\tend", "end)"]
    elif spec.trigger == "click":
        out += [f"detector.MouseClick:Connect({fn})"]
    elif spec.trigger == "prompt":
        out += [f"prompt.Triggered:Connect({fn})"]
    elif spec.trigger == "spawn":
        out += ["Players.PlayerAdded:Connect(function(player)", "\tplayer.CharacterAdded:Connect(function(character)",
                '\t\tcharacter:WaitForChild("Humanoid")', f"\t\t{fn}(player)", "\tend)", "end)"]
    elif spec.trigger == "timer":
        out += [f"local INTERVAL = {spec.interval}", "", "while true do", "\ttask.wait(INTERVAL)",
                "\tfor _, player in Players:GetPlayers() do", f"\t\t{fn}(player)", "\tend", "end"]
    elif spec.trigger == "chat":
        out += [f'local COMMAND = "{spec.command}"', "", "Players.PlayerAdded:Connect(function(player)",
                "\tplayer.Chatted:Connect(function(message)", "\t\tif string.lower(message) == COMMAND then",
                f"\t\t\t{fn}(player)", "\t\tend", "\tend)", "end)"]
    return "\n".join(out) + "\n"


def build_checks(spec: Spec) -> list[tuple[str, str]]:
    checks = [TRIGGER_CHECKS[spec.trigger]]
    for e, p in spec.effects:
        checks += effect_checks(e, p)
    if spec.color:
        rgb = spec.color[1]
        checks.append((f"part colour {spec.color[0]}", rf"Color3\.fromRGB\(\s*{rgb[0]}\s*,\s*{rgb[1]}\s*,\s*{rgb[2]}\s*\)"))
    if spec.part_name:
        checks.append((f"part named {spec.part_name}", re.escape(f'"{spec.part_name}"')))
    if spec.cooldown is not None:
        checks.append((f"cooldown {_num(spec.cooldown)}", rf"\b{re.escape(_num(spec.cooldown))}\b"))
    if spec.once:
        checks.append(("once per player", r"\[player\]\s*=\s*true"))
    if spec.interval:
        checks.append((f"every {spec.interval}s", rf"\b{spec.interval}\b"))
    if spec.command:
        checks.append((f"command {spec.command}", re.escape(f'"{spec.command}"')))
    return checks


# ----------------------------------------------------------------------------- language

WHO = {  # (accusative, dative, genitive) for one player / all players
    "en": {"one": [("the player", "the player", "the player's"), ("them", "them", "their")],
           "all": [("everyone", "everyone", "everyone's"), ("all players", "all players", "all players'")]},
    "fr": {"one": [("le joueur", "au joueur", "du joueur")],
           "all": [("tous les joueurs", "à tous les joueurs", "de tous les joueurs")]},
    "es": {"one": [("al jugador", "al jugador", "del jugador")],
           "all": [("a todos los jugadores", "a todos los jugadores", "de todos los jugadores"),
                   ("a todos", "a todos", "de todos")]},
}


def _secs(lang: str, d) -> str:
    return {"en": f"{d} seconds", "fr": f"{d} secondes", "es": f"{d} segundos"}[lang]


def effect_phrase(lang: str, effect: str, p: dict, who: tuple, rng: random.Random, gender: str = "m") -> str:
    """Third-person verb phrase ('heals the player by 20'). FR/ES 2nd-person imperatives are derived from it."""
    acc, dat, gen = who
    c = rng.choice
    n = p.get("n")
    if lang == "en":
        if effect == "kill":
            return c([f"kills {acc}", f"instantly kills {acc}", f"kills {acc} instantly"])
        if effect == "damage":
            return c([f"deals {n} damage to {dat}", f"does {n} damage to {dat}", f"damages {acc} by {n}",
                      f"takes {n} health from {dat}"])
        if effect == "heal":
            if n is None:
                return c([f"fully heals {acc}", f"heals {acc} to full health", f"restores {gen} health"])
            return c([f"heals {acc} by {n}", f"gives {dat} {n} health", f"heals {acc} for {n} hp", f"restores {n} health"])
        if effect == "speed":
            s = c([f"sets {gen} walkspeed to {n}", f"makes {acc} run at speed {n}", f"gives {dat} speed {n}",
                   f"changes {gen} WalkSpeed to {n}"])
            return s + (c([f" for {p['d']} seconds", f" for {p['d']}s"]) if p["d"] else "")
        if effect == "jump":
            return c([f"sets {gen} jump power to {n}", f"makes {acc} jump higher (jump power {n})", f"gives {dat} {n} jump power"])
        if effect == "stat":
            return c([f"gives {dat} {n} {p['stat']}", f"adds {n} {p['stat']} to {gen} leaderstats", f"awards {n} {p['stat']}"])
        if effect == "teleport":
            if "xyz" in p:
                x, y, z = p["xyz"]
                return c([f"teleports {acc} to {x}, {y}, {z}", f"teleports {acc} to position ({x}, {y}, {z})",
                          f"sends {acc} to {x} {y} {z}"])
            return c([f"teleports {acc} to the part called {p['target']}", f"teleports {acc} to {p['target']}",
                      f"sends {acc} to the {p['target']} part"])
        if effect == "launch":
            s = c([f"launches {acc} into the air", f"flings {acc} upward", f"launches {acc} up"])
            return s + (c([f" with force {n}", f" with {n} velocity", f" at {n} speed"]) if n else "")
        if effect == "forcefield":
            return c([f"gives {dat} a forcefield for {p['d']} seconds", f"puts a force field on {acc} for {p['d']}s",
                      f"makes {acc} invincible with a ForceField for {p['d']} seconds"])
        if effect == "fx":
            k = {"Fire": "fire", "Sparkles": "sparkles", "Smoke": "smoke"}[p["kind"]]
            return c([f"puts {k} on {acc} for {p['d']} seconds", f"gives {dat} {k} for {p['d']}s",
                      f"adds a {k} effect to {acc} for {p['d']} seconds"])
        if effect == "scale":
            f = _num(p["f"])
            if p["f"] < 1:
                return c([f"shrinks {acc} to scale {f}", f"makes {acc} smaller (scale {f})"])
            return c([f"makes {acc} {f}x bigger", f"scales {acc} to {f}", f"makes {acc} grow to size {f}"])
        if effect == "explode":
            return c([f"makes {acc} explode", f"blows {acc} up", f"creates an explosion on {acc}"])
        if effect == "tool":
            return c([f"gives {dat} the {p['name']} tool from ServerStorage", f"gives {dat} a {p['name']}",
                      f"puts the {p['name']} from ServerStorage in {gen} backpack"])
        if effect == "sit":
            return c([f"makes {acc} sit down", f"forces {acc} to sit"])
        if effect == "maxhealth":
            return c([f"sets {gen} max health to {n}", f"gives {dat} {n} max health", f"changes {gen} MaxHealth to {n}"])
        if effect == "color":
            if p["c"] is None:
                return c(["changes to a random color", "turns a random colour", "changes color randomly"])
            return c([f"turns {p['c'][2]}", f"changes color to {p['c'][2]}", f"becomes {p['c'][2]}"])
        if effect == "hide":
            return c([f"disappears for {p['d']} seconds", f"vanishes for {p['d']}s then comes back",
                      f"becomes invisible for {p['d']} seconds"])
    if lang == "fr":
        if effect == "kill":
            return c([f"tue {acc}", f"tue {acc} instantanément", f"fait mourir {acc}"])
        if effect == "damage":
            return c([f"inflige {n} dégâts {dat}", f"fait {n} de dégâts {dat}", f"enlève {n} PV {dat}"])
        if effect == "heal":
            if n is None:
                return c([f"soigne {acc} complètement", f"remet la vie {gen} au maximum", f"soigne {acc} à fond"])
            return c([f"soigne {acc} de {n} PV", f"donne {n} points de vie {dat}", f"rend {n} PV {dat}"])
        if effect == "speed":
            s = c([f"met la vitesse {gen} à {n}", f"change la WalkSpeed {gen} à {n}", f"fait courir {acc} à {n} de vitesse"])
            return s + (c([f" pendant {p['d']} secondes", f" pour {p['d']} secondes"]) if p["d"] else "")
        if effect == "jump":
            return c([f"met la puissance de saut {gen} à {n}", f"fait sauter {acc} plus haut (JumpPower {n})"])
        if effect == "stat":
            return c([f"donne {n} {p['stat']} {dat}", f"ajoute {n} {p['stat']} {dat}", f"rapporte {n} {p['stat']}"])
        if effect == "teleport":
            if "xyz" in p:
                x, y, z = p["xyz"]
                return c([f"téléporte {acc} à {x}, {y}, {z}", f"téléporte {acc} à la position ({x}, {y}, {z})"])
            return c([f"téléporte {acc} vers la part {p['target']}", f"téléporte {acc} à {p['target']}"])
        if effect == "launch":
            s = c([f"propulse {acc} dans les airs", f"envoie {acc} en l'air", f"fait voler {acc} vers le haut"])
            return s + (c([f" avec une force de {n}", f" à {n} de vitesse"]) if n else "")
        if effect == "forcefield":
            return c([f"donne un champ de force {dat} pendant {p['d']} secondes",
                      f"met un ForceField sur {acc} pendant {p['d']} secondes"])
        if effect == "fx":
            k = {"Fire": "du feu", "Sparkles": "des étincelles", "Smoke": "de la fumée"}[p["kind"]]
            return c([f"met {k} sur {acc} pendant {p['d']} secondes", f"ajoute {k} {dat} pendant {p['d']} secondes"])
        if effect == "scale":
            f = _num(p["f"])
            if p["f"] < 1:
                return c([f"rétrécit {acc} à l'échelle {f}", f"rend {acc} plus petit (échelle {f})"])
            return c([f"rend {acc} {f} fois plus grand", f"agrandit {acc} à l'échelle {f}"])
        if effect == "explode":
            return c([f"fait exploser {acc}", f"crée une explosion sur {acc}"])
        if effect == "tool":
            return c([f"donne l'outil {p['name']} de ServerStorage {dat}", f"donne un {p['name']} {dat}"])
        if effect == "sit":
            return c([f"fait asseoir {acc}", f"force {acc} à s'asseoir"])
        if effect == "maxhealth":
            return c([f"met la vie max {gen} à {n}", f"change la MaxHealth {gen} à {n}"])
        if effect == "color":
            if p["c"] is None:
                return c(["prend une couleur aléatoire", "change de couleur au hasard"])
            col = p["c"][3][1 if gender == "f" else 0]
            return c([f"devient {col}", f"change de couleur en {p['c'][3][0]}"])
        if effect == "hide":
            return c([f"disparaît pendant {p['d']} secondes", f"devient invisible pendant {p['d']} secondes puis revient"])
    if lang == "es":
        if effect == "kill":
            return c([f"mata {acc}", f"mata {acc} al instante", f"elimina {acc}"])
        if effect == "damage":
            return c([f"hace {n} de daño {dat}", f"quita {n} de vida {dat}", f"causa {n} de daño {dat}"])
        if effect == "heal":
            if n is None:
                return c([f"cura {acc} por completo", f"restaura toda la vida {gen}", f"cura {acc} al máximo"])
            return c([f"cura {acc} {n} de vida", f"da {n} de vida {dat}", f"cura {n} puntos de vida {dat}"])
        if effect == "speed":
            s = c([f"pone la velocidad {gen} en {n}", f"cambia la WalkSpeed {gen} a {n}", f"hace correr {acc} a velocidad {n}"])
            return s + (c([f" durante {p['d']} segundos", f" por {p['d']} segundos"]) if p["d"] else "")
        if effect == "jump":
            return c([f"pone el poder de salto {gen} en {n}", f"hace saltar más alto {acc} (JumpPower {n})"])
        if effect == "stat":
            return c([f"da {n} {p['stat']} {dat}", f"suma {n} {p['stat']} {dat}", f"regala {n} {p['stat']} {dat}"])
        if effect == "teleport":
            if "xyz" in p:
                x, y, z = p["xyz"]
                return c([f"teletransporta {acc} a {x}, {y}, {z}", f"teletransporta {acc} a la posición ({x}, {y}, {z})"])
            return c([f"teletransporta {acc} a la parte {p['target']}", f"teletransporta {acc} a {p['target']}"])
        if effect == "launch":
            s = c([f"lanza {acc} por los aires", f"lanza {acc} hacia arriba", f"manda a volar {acc}"])
            return s + (c([f" con una fuerza de {n}", f" a velocidad {n}"]) if n else "")
        if effect == "forcefield":
            return c([f"da un campo de fuerza {dat} durante {p['d']} segundos",
                      f"pone un ForceField {dat} por {p['d']} segundos"])
        if effect == "fx":
            k = {"Fire": "fuego", "Sparkles": "chispas", "Smoke": "humo"}[p["kind"]]
            return c([f"pone {k} {dat} durante {p['d']} segundos", f"añade {k} {dat} por {p['d']} segundos"])
        if effect == "scale":
            f = _num(p["f"])
            if p["f"] < 1:
                return c([f"encoge {acc} a escala {f}", f"hace más pequeño {acc} (escala {f})"])
            return c([f"hace {f} veces más grande {acc}", f"agranda {acc} a escala {f}"])
        if effect == "explode":
            return c([f"hace explotar {acc}", f"crea una explosión sobre {acc}"])
        if effect == "tool":
            return c([f"da la herramienta {p['name']} de ServerStorage {dat}", f"da una {p['name']} {dat}"])
        if effect == "sit":
            return c([f"hace sentar {acc}", f"obliga {acc} a sentarse"])
        if effect == "maxhealth":
            return c([f"pone la vida máxima {gen} en {n}", f"cambia la MaxHealth {gen} a {n}"])
        if effect == "color":
            if p["c"] is None:
                return c(["cambia a un color aleatorio", "cambia de color al azar"])
            col = p["c"][4][1 if gender == "f" else 0]
            return c([f"se vuelve {col}", f"cambia a color {p['c'][4][0]}"])
        if effect == "hide":
            return c([f"desaparece durante {p['d']} segundos", f"se vuelve invisible por {p['d']} segundos y luego vuelve"])
    raise KeyError((lang, effect))


_EN_IMP = {"kills": "kill", "instantly": "instantly", "deals": "deal", "does": "do", "damages": "damage", "takes": "take",
           "fully": "fully", "heals": "heal", "restores": "restore", "gives": "give", "sets": "set", "makes": "make",
           "changes": "change", "adds": "add", "awards": "award", "teleports": "teleport", "sends": "send",
           "launches": "launch", "flings": "fling", "puts": "put", "shrinks": "shrink", "scales": "scale",
           "blows": "blow", "creates": "create", "forces": "force"}
_FR_IMP = {"met": "mets", "rend": "rends", "fait": "fais", "remet": "remets", "inflige": "inflige", "rétrécit": "rétrécis",
           "agrandit": "agrandis"}
_ES_IMP = {"pone": "pon", "hace": "haz"}


def imperative(lang: str, phrase: str) -> str:
    first, _, rest = phrase.partition(" ")
    table = {"en": _EN_IMP, "fr": _FR_IMP, "es": _ES_IMP}[lang]
    return (table.get(first, first) + (" " + rest if rest else "")).strip()


def _join(lang: str, parts: list[str]) -> str:
    return {"en": " and ", "fr": " et ", "es": " y "}[lang].join(parts)


ASK = {"en": ["", "", "make ", "make me ", "create ", "i want ", "i need ", "can you make ", "build ", "script ", "code ",
              "write a script for ", "pls make ", "add "],
       "fr": ["", "", "fais ", "fais-moi ", "crée ", "je veux ", "j'ai besoin d'", "tu peux faire ", "code ",
              "écris un script pour ", "ajoute "],
       "es": ["", "", "haz ", "hazme ", "crea ", "quiero ", "necesito ", "puedes hacer ", "programa ",
              "escribe un script para ", "añade "]}
SENT_PREFIX = {"en": ["", "", "script: ", "write a script: ", "make it so "],
               "fr": ["", "", "script : ", "écris un script : ", "fais en sorte que "],
               "es": ["", "", "script: ", "escribe un script: ", "haz que "]}
TAIL = {"en": ["", "", "", " please", " pls", " in roblox", " for my game", " (server script)"],
        "fr": ["", "", "", " stp", " s'il te plaît", " dans roblox", " pour mon jeu", " svp"],
        "es": ["", "", "", " por favor", " porfa", " en roblox", " para mi juego", " pls"]}
REL = {"en": "that", "fr": "qui", "es": "que"}


def _named(lang: str, name: str) -> str:
    return {"en": f" called {name}", "fr": f" nommé{''} {name}", "es": f" llamado {name}"}[lang]


def _object_phrase(lang: str, spec: Spec, rng: random.Random) -> tuple[str, str, str]:
    """(indefinite noun phrase incl. colour/name, definite noun phrase, gender)"""
    row = rng.choice(OBJECTS[spec.trigger][lang])
    indef, definite = row[0], row[1]
    gender = row[2] if len(row) > 2 else "m"
    if spec.color:
        col = {"en": spec.color[2], "fr": spec.color[3][1 if gender == "f" else 0],
               "es": spec.color[4][1 if gender == "f" else 0]}[lang]
        if lang == "en":
            art, _, noun = indef.partition(" ")
            indef = f"a {col} {noun}"
        else:
            art, _, noun = indef.partition(" ")
            head, _, tail = noun.partition(" ")
            indef = f"{art} {head} {col}" + (f" {tail}" if tail else "")
    if spec.part_name:
        if lang == "fr":
            indef += f" nommé{'e' if gender == 'f' else ''} {spec.part_name}"
        elif lang == "es":
            indef += f" llamad{'a' if gender == 'f' else 'o'} {spec.part_name}"
        else:
            indef += f" called {spec.part_name}"
    return indef, definite, gender


def _trigger_clause(lang: str, spec: Spec, definite: str, gender: str, rng: random.Random) -> str:
    c = rng.choice
    t = spec.trigger
    if lang == "en":
        return {"touch": c(["when touched", "when a player touches it", "when you touch it", "on touch",
                            f"when someone steps on {definite}"]),
                "click": c(["when clicked", "when a player clicks it", "when you click it"]),
                "prompt": c(["when you press E", "when the prompt is triggered", "when a player uses the prompt"]),
                "spawn": c(["when a player spawns", "every time a player respawns", "when a player's character loads"]),
                "timer": c([f"every {spec.interval} seconds", f"every {spec.interval}s"]),
                "chat": c([f'when a player types "{spec.command}" in chat', f'when someone says {spec.command}',
                           f'with the chat command {spec.command}'])}[t]
    pron = {"fr": "la" if gender == "f" else "le", "es": "la" if gender == "f" else "lo"}.get(lang)
    if lang == "fr":
        return {"touch": c(["au toucher", f"quand un joueur {pron} touche", f"quand on {pron} touche",
                            "quand on marche dessus"]),
                "click": c(["quand on clique dessus", "au clic", f"quand un joueur {pron} clique"]),
                "prompt": c(["quand on appuie sur E", "quand le prompt est activé", "quand un joueur utilise le prompt"]),
                "spawn": c(["quand un joueur apparaît", "à chaque réapparition", "quand le personnage d'un joueur apparaît"]),
                "timer": c([f"toutes les {spec.interval} secondes", f"chaque {spec.interval} secondes"]),
                "chat": c([f'quand un joueur écrit "{spec.command}" dans le chat', f"avec la commande {spec.command}"])}[t]
    return {"touch": c(["al tocar" + ("la" if gender == "f" else "lo"), f"cuando un jugador {pron} toca",
                        "cuando alguien la pisa" if gender == "f" else "cuando alguien lo pisa"]),
            "click": c(["al hacer clic", f"cuando haces clic en {'ella' if gender == 'f' else 'él'}", "cuando un jugador hace clic"]),
            "prompt": c(["al presionar E", "cuando se activa el prompt", "cuando un jugador usa el prompt"]),
            "spawn": c(["cuando un jugador aparece", "cada vez que un jugador reaparece", "cuando aparece el personaje"]),
            "timer": c([f"cada {spec.interval} segundos"]),
            "chat": c([f'cuando un jugador escribe "{spec.command}" en el chat', f"con el comando {spec.command}"])}[t]


def _modifier_phrase(lang: str, spec: Spec, rng: random.Random) -> str:
    c = rng.choice
    if spec.once:
        return c({"en": [" only once per player", " (once per player)"],
                  "fr": [" une seule fois par joueur", " (une fois par joueur)"],
                  "es": [" solo una vez por jugador", " (una vez por jugador)"]}[lang])
    if spec.cooldown is not None:
        cd = _num(spec.cooldown)
        return c({"en": [f" with a {cd} second cooldown", f" ({cd}s cooldown)"],
                  "fr": [f" avec un cooldown de {cd} secondes", f" avec un délai de {cd} secondes"],
                  "es": [f" con un cooldown de {cd} segundos", f" con {cd} segundos de espera"]}[lang])
    return ""


def _strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def phrase(spec: Spec, rng: random.Random) -> str:
    lang = spec.lang
    plural = spec.trigger == "timer"
    who = rng.choice(WHO[lang]["all" if plural else "one"])
    has_part_effect = any(e in PART_ONLY for e in spec.effect_names)
    if spec.trigger in PART_TRIGGERS:
        indef, definite, gender = _object_phrase(lang, spec, rng)
        effs = [effect_phrase(lang, e, p, who, rng, gender) for e, p in spec.effects]
        clause = _trigger_clause(lang, spec, definite, gender, rng)
        if has_part_effect or rng.random() < 0.65:
            text = f"{rng.choice(ASK[lang])}{indef} {REL[lang]} {_join(lang, effs)} {clause}"
        else:  # sentence form: "when someone touches the part, heal the player"
            clause2 = {"en": {"touch": f"when someone touches {definite}", "click": f"when someone clicks {definite}",
                              "prompt": f"when someone triggers {definite}"},
                       "fr": {"touch": f"quand quelqu'un touche {definite}", "click": f"quand quelqu'un clique sur {definite}",
                              "prompt": f"quand quelqu'un active {definite}"},
                       "es": {"touch": f"cuando alguien toca {definite}", "click": f"cuando alguien hace clic en {definite}",
                              "prompt": f"cuando alguien activa {definite}"}}[lang][spec.trigger]
            obj = indef.split(" ", 1)[1] if " " in indef else indef
            lead = {"en": f"make {indef}; ", "fr": f"fais {indef} ; ", "es": f"haz {indef}; "}[lang] \
                if (spec.color or spec.part_name) else ""
            del obj
            imps = [imperative(lang, e) for e in effs]
            text = f"{lead}{rng.choice(SENT_PREFIX[lang]) if not lead else ''}{clause2}, {_join(lang, imps)}"
    else:
        effs = [effect_phrase(lang, e, p, who, rng) for e, p in spec.effects]
        clause = _trigger_clause(lang, spec, "", "m", rng)
        imps = [imperative(lang, e) for e in effs]
        if rng.random() < 0.5:
            text = f"{rng.choice(SENT_PREFIX[lang])}{clause}, {_join(lang, imps)}"
        else:
            text = f"{rng.choice(SENT_PREFIX[lang])}{_join(lang, imps)} {clause}"
        text = text.replace("make it so ", "make it so ") if lang == "en" else text
    text += _modifier_phrase(lang, spec, rng) + rng.choice(TAIL[lang])
    text = re.sub(r"\s+", " ", text).strip()
    if lang != "en" and rng.random() < 0.2:
        text = _strip_accents(text)
    if rng.random() < 0.35:
        text = text[0].upper() + text[1:]
    if rng.random() < 0.15:
        text += rng.choice(["!", ".", "?"])
    return text


# ----------------------------------------------------------------------------- sampling

def random_spec(rng: random.Random, lang: str | None = None, trigger: str | None = None,
                effects: list[str] | None = None) -> Spec:
    lang = lang or rng.choice(LANGS)
    trigger = trigger or rng.choice(TRIGGERS)
    if effects is None:
        pool = [e for e in EFFECTS if (trigger in PART_TRIGGERS or e not in PART_ONLY)
                and not (trigger == "spawn" and e in NOT_ON_SPAWN)]
        first = rng.choice(pool)
        effects = [first]
        if first not in ALONE and rng.random() < 0.35:
            second = rng.choice([e for e in pool if e not in ALONE and e != first])
            effects.append(second)
    spec = Spec(lang, trigger, [(e, _params(e, rng, lang)) for e in effects])
    if trigger in PART_TRIGGERS:
        if rng.random() < 0.35:
            spec.color = rng.choice(COLORS)
        if rng.random() < 0.25:
            spec.part_name = rng.choice(PART_NAMES) + rng.choice(["", "1", "A", "Main"])
        # a colour in the request would clash with a recolour effect asking for another colour
        if any(e == "color" for e in spec.effect_names):
            spec.color = None
    if trigger in ("touch", "click", "prompt", "chat"):
        r = rng.random()
        if r < 0.2:
            spec.cooldown = rng.choice([0.5, 1, 2, 3, 5, 10, 30])
        elif r < 0.3:
            spec.once = True
    if trigger == "timer":
        spec.interval = rng.choice([5, 10, 15, 30, 60, 120])
    if trigger == "chat":
        word = {"kill": "kill", "damage": "hurt", "heal": "heal", "speed": "speed", "jump": "jump", "stat": "reward",
                "teleport": "tp", "launch": "launch", "forcefield": "shield", "fx": "fx", "scale": "size",
                "explode": "boom", "tool": "tool", "sit": "sit", "maxhealth": "hp"}[spec.effect_names[0]]
        spec.command = rng.choice(["/", "!", ";"]) + word
    return spec


def verify(src: str) -> bool:
    return check_structure(src).ok and not lint(src)


def make_sample(spec: Spec, rng: random.Random) -> ComposedSample:
    return ComposedSample(spec, phrase(spec, rng), build_code(spec), build_checks(spec))


def generate(n: int, seed: int = 0, split: str = "train") -> tuple[list[ComposedSample], int]:
    """``split='train'`` excludes every held-out (trigger, effect) pair; ``'heldout'`` yields ONLY specs containing one."""
    rng = random.Random(seed)
    out, rejected, seen, tries = [], 0, set(), 0
    held = sorted(HELD_OUT_PAIRS)
    while len(out) < n and tries < n * 20:
        tries += 1
        if split == "heldout":
            trig, eff = rng.choice(held)
            spec = random_spec(rng, trigger=trig, effects=[eff])
        else:
            spec = random_spec(rng)
            if spec.held_out():
                continue
        s = make_sample(spec, rng)
        key = (s.prompt, s.solution)
        if key in seen:
            continue
        seen.add(key)
        if verify(s.solution):
            out.append(s)
        else:
            rejected += 1
    return out, rejected


def grade(spec_checks: list[tuple[str, str]], code: str) -> dict:
    """Which of a request's checks a generated script satisfies (static: API + exact numbers, not a playtest)."""
    results = [(d, bool(re.search(rx, code))) for d, rx in spec_checks]
    ok_v = verify(code)
    passed = sum(ok for _, ok in results)
    return {"verified": ok_v, "checks_passed": passed, "checks_total": len(results),
            "missing": [d for d, ok in results if not ok], "correct": ok_v and passed == len(results)}
