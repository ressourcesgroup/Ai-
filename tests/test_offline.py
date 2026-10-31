"""Offline (serverless) plugin engine: generated data + matcher behaviour."""

import json
import re
from pathlib import Path

import pytest

from godmode.export_offline import _luau_quote, build_entries, render
from godmode.luau import check_structure, lint, tokenize

PLUGIN = Path(__file__).resolve().parent.parent / "plugin" / "src"
ENTRIES = build_entries()
ANIMATION_WORDS = ["animation", "animate", "anim", "emote", "keyframe"]


def match(message: str):
    """Python reference of OfflineEngine.match."""
    text = message.lower()
    wants_animation = any(w in text for w in ANIMATION_WORDS)
    best, best_score = None, 0
    for e in ENTRIES:
        score = sum(k in text for k in e["keywords"]) * 2
        if score > 0 and e["animation"] == wants_animation:
            score += 1
        if score > best_score:
            best, best_score = e, score
    return best


def test_generated_module_is_current_and_valid():
    src = render(ENTRIES)
    assert (PLUGIN / "OfflineData.luau").read_text() == src, "run: python -m godmode.export_offline"
    assert check_structure(src).ok


def test_every_offline_response_is_verified():
    assert len(ENTRIES) == 30
    for e in ENTRIES:
        r = e["response"]
        assert r["source"].startswith("offline:tool:")
        assert r["actions"]
        assert all(v["ok"] for v in r["verification"])
        json.dumps(r)  # serialisable


def test_matcher_routes_common_requests():
    cases = {
        "build an obby": "obby",
        "build me a house": "house",
        "save player data": "datastore",
        "add a leaderboard with cash": "leaderstats",
        "make a sprint animation": "anim_run",
        "chase animation like fugitive": "anim_run",
        "make a fight combo animation": "anim_punch",
        "aw shucks emote": "anim_shucks",
        "squid game elimination animation": "anim_knockdown",
        "make a dance emote": "anim_dance",
        "make an npc guard that patrols": "npc_patrol",
        # French / Spanish
        "construis une maison": "house",
        "haz una casa": "house",
        "fais une porte qui s'ouvre": "tween_door",
        "quiero guardar los datos del jugador": "datastore",
        "crée un pnj ennemi qui patrouille": "npc_patrol",
        "une plateforme mobile": "moving_platform",
        "fais une animation de danse": "anim_dance",
        "animación de saltar": "anim_jump",
        "construye una torre": "tower",
        "fais une voiture": "car",
        # GUI designer (real ScreenGui, no scripts) vs scripted HUD
        "make a main menu gui": "gui",
        "crée une interface de boutique": "gui",
        "haz una tienda con botones": "gui",
        "make a working hud script": "ui_hud",
    }
    for message, key in cases.items():
        got = match(message)
        assert got is not None and got["key"] == key, (message, got and got["key"])
    assert match("what is the meaning of life") is None


def test_plugin_sources_parse_and_offline_is_default():
    for f in PLUGIN.glob("*.luau"):
        assert check_structure(f.read_text()).ok, f.name
    main = (PLUGIN / "Main.luau").read_text()
    assert "readSetting(SETTING_USE_SERVER) == true" in main
    assert "OfflineEngine.chat(payload)" in main
    assert re.search(r"if not useServer then\s+return true, \"Offline mode", main)


@pytest.mark.parametrize("text", ["plain", 'say "hi"', "back\\slash", "two\nlines\r\t", "bell\x07 del\x7f", "écran de chargement", "plataforma móvil", "ñ ü ç 日本"])
def test_luau_quote_produces_valid_luau_literals(text):
    lit = _luau_quote(text)
    toks = tokenize(f"local s = {lit}")
    assert [t.kind for t in toks].count("string") == 1
    assert "\\u" not in lit  # never a JSON-style \u escape
    if text.isprintable():
        assert lit == '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def test_json_style_escapes_would_break_the_module():
    """Regression for the shipped bug: json.dumps("écran") is a malformed Luau escape."""
    assert not check_structure(f"local k = {json.dumps('écran')}\n").ok
    src = render(ENTRIES)
    kw_lines = [ln for ln in src.splitlines() if ln.lstrip().startswith(("key =", "keywords ="))]
    assert len(kw_lines) == 2 * len(ENTRIES)
    assert not any("\\u" in ln for ln in kw_lines)
    assert any("é" in ln for ln in kw_lines)  # accents are raw UTF-8
    for e in ENTRIES:
        for k in e["keywords"]:
            assert _luau_quote(k) in src
