"""Export the plugin's offline engine data: real, verified server responses embedded in Luau.

Instead of re-implementing the tools in Luau (which we cannot execute here),
we run the Python service once per offline intent and embed its exact /chat
response as JSON. The plugin matches a request to an intent by keywords and
decodes the JSON with HttpService:JSONDecode (no HTTP permission needed).

Run: python -m godmode.export_offline   (rewrites plugin/src/OfflineData.luau)
"""

from __future__ import annotations

import json
from pathlib import Path

from .anim import list_styles
from .server.service import GodmodeService

OUT = Path("plugin/src/OfflineData.luau")

# (key, prompt sent to the real service, keywords the plugin matches on)
TOOL_INTENTS = [
    ("obby", "build an obby with 10 platforms", ["obby", "parkour", "obstacle", "platform", "kill brick", "parcours", "obstáculo"]),
    ("house", "build a house", ["house", "home", "room", "building", "cabin", "maison", "casa", "cabane", "cabaña"]),
    ("tower", "build a tower", ["tower", "skyscraper", "une tour", "la tour", "torre", "gratte-ciel", "rascacielos"]),
    ("staircase", "build a staircase", ["stair", "steps", "staircase", "escalier", "escalera"]),
    ("arena", "build an arena with spawn points", ["arena", "map", "battleground", "spawn point", "arène"]),
    ("tree", "make a tree", ["tree", "forest", "arbre", "árbol", "arbol", "forêt", "bosque"]),
    ("car", "build a car", ["car", "vehicle", "kart", "voiture", "coche", "véhicule", "vehículo"]),
    ("remote_event", "create a validated remote event", ["remote", "remoteevent", "fireserver", "client to server", "network", "remoto", "événement distant", "evento remoto"]),
    ("leaderstats", "add leaderstats with coins", ["leaderstats", "leaderboard", "coins", "cash", "stats", "classement", "pièces", "monedas", "argent", "dinero"]),
    ("datastore", "save player data with datastores", ["datastore", "save", "load", "data store", "persist", "sauvegard", "guardar", "données", "datos", "charger", "cargar"]),
    ("gui", "make a main menu with play, settings and quit buttons", ["gui", "ui", "hud", "menu", "screengui", "screen gui", "button", "interface", "interfaz", "health bar", "barre de vie", "barra de vida", "bouton", "botón", "shop", "boutique", "tienda", "inventory", "inventaire", "inventario", "loading screen", "écran de chargement"]),
    ("ui_hud", "make a working hud script with a health bar", ["hud script", "scripted hud", "gui script", "working hud", "localscript"]),
    ("npc_patrol", "make an npc that patrols", ["npc", "patrol", "enemy", "pathfinding", "pnj", "ennemi", "enemigo", "patrouill", "patrull"]),
    ("tween_door", "make a door that tweens open", ["door", "tween", "gate", "porte", "puerta", "portail", "ouvr", "abre", "abrir"]),
    ("moving_platform", "make a moving platform", ["moving platform", "elevator", "lift", "plateforme mobile", "plataforma móvil", "mobile", "móvil", "movil", "ascenseur", "ascensor"]),
]

ANIM_SYNONYMS = {
    "idle": ["idle", "breathing", "standing", "repos", "reposo"],
    "walk": ["walk", "walking", "marche", "marcher", "caminar", "camina"],
    "run": ["run", "sprint", "chase", "running", "fugitive", "courir", "course", "correr", "carrera"],
    "jump": ["jump", "leap", "saut", "sauter", "saltar", "salto"],
    "punch": ["punch", "fight", "combo", "jab", "hook", "attack", "combat", "coup de poing", "poing", "puñetazo", "golpe", "ataque", "attaque"],
    "kick": ["kick", "coup de pied", "patada"],
    "block": ["block", "guard up", "defend", "bloquer", "bloquear", "défendre", "defender"],
    "hit": ["hit reaction", "stagger", "get hit", "hurt", "flinch", "blessé", "herido"],
    "knockdown": ["knockdown", "knock down", "eliminat", "fall", "death", "die", "squid", "mort", "muerte", "morir", "tomber", "caer"],
    "victory": ["victory", "win", "celebrate", "victoire", "victoria", "gagner", "ganar", "célébr", "celebrar"],
    "wave": ["wave", "hello", "greet", "saluer", "saludar", "bonjour", "hola"],
    "shucks": ["shucks", "shy", "embarrass", "sheepish", "awkward", "timide", "tímido", "gêné", "avergonzado"],
    "dance": ["dance", "dancing", "danse", "danser", "baile", "bailar"],
    "sit": ["sit", "sitting", "assis", "asseoir", "sentar"],
    "crouch": ["crouch", "sneak", "stealth", "accroupi", "agachar", "furtif", "sigilo"],
}


def _luau_quote(text: str) -> str:
    """A double-quoted Luau string literal for `text`.

    Not json.dumps: JSON writes non-ASCII as `\\u00e9`, which is a *malformed escape*
    in Luau (it requires `\\u{e9}`), so a single accented keyword made the whole
    generated module fail to compile. Non-ASCII text is kept as raw UTF-8, which
    Luau string literals accept; only the characters that need it are escaped.
    """
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _long_string(text: str) -> str:
    level = 0
    while f"]{'=' * level}]" in text:
        level += 1
    eq = "=" * level
    return f"[{eq}[{text}]{eq}]"


def build_entries(service: GodmodeService | None = None) -> list[dict]:
    service = service or GodmodeService()
    entries = []
    intents = [(k, p, kw, False) for k, p, kw in TOOL_INTENTS]
    intents += [(f"anim_{s}", f"make a {s} animation", ANIM_SYNONYMS[s], True) for s in (st["name"] for st in list_styles())]
    for key, prompt, keywords, is_anim in intents:
        reply = service.chat({"message": prompt, "autonomy": 3, "engine": "tool"})
        reply.pop("thinking", None)  # timings differ per run; the plugin builds its own honest offline trace
        if not reply.get("actions") or not str(reply.get("source", "")).startswith("tool:"):
            raise RuntimeError(f"offline intent {key!r} did not produce a tool response: {reply.get('source')}")
        if not all(v.get("ok") for v in reply.get("verification", [])):
            raise RuntimeError(f"offline intent {key!r} has failing verification")
        reply["source"] = "offline:" + reply["source"]
        reply.setdefault("warnings", []).append(
            "Offline mode: this is the default version of this build/script. Edit it after applying, "
            "or enable the optional GODMODE server (/server on) for custom sizes, counts and colors."
        )
        entries.append({"key": key, "keywords": keywords, "animation": is_anim, "response": reply})
    return entries


def render(entries: list[dict]) -> str:
    lines = [
        "--!strict",
        "-- GENERATED by `python -m godmode.export_offline` -- do not edit by hand.",
        "-- Each entry is a real, verified response of the GODMODE tool server, embedded as JSON",
        "-- so the plugin works with no server. Decoded with HttpService:JSONDecode.",
        "",
        "export type Entry = { key: string, keywords: { string }, animation: boolean, json: string }",
        "",
        "local entries: { Entry } = {",
    ]
    for e in entries:
        kw = ", ".join(_luau_quote(k) for k in e["keywords"])
        lines.append("\t{")
        lines.append(f"\t\tkey = {_luau_quote(e['key'])},")
        lines.append(f"\t\tkeywords = {{ {kw} }},")
        lines.append(f"\t\tanimation = {'true' if e['animation'] else 'false'},")
        lines.append(f"\t\tjson = {_long_string(json.dumps(e['response'], separators=(',', ':')))},")
        lines.append("\t},")
    lines += ["}", "", "return entries", ""]
    return "\n".join(lines)


def main() -> None:
    entries = build_entries()
    OUT.write_text(render(entries))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB, {len(entries)} offline intents)")


if __name__ == "__main__":
    main()
