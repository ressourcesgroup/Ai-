"""GUI designer: real ScreenGui instance trees (no scripts), EN/FR/ES parsing, routing, never model-first."""

import pytest

from godmode.server.retrieval import Retriever
from godmode.server.service import MODEL_FIRST, GodmodeService
from godmode.tools import route, run_tool
from godmode.tools import gui
from godmode.tools.base import validate_tree

from test_multilingual import _FakeModel

KIND_PROMPTS = {
    "menu": "make a main menu with play, settings and quit buttons",
    "shop": "make a shop selling swords, bows and potions",
    "inventory": "make an inventory with 30 slots",
    "settings": "make a settings menu",
    "dialog": "make a popup asking are you sure with yes and no buttons",
    "loading": "make a loading screen",
    "timer": "make a round timer gui",
    "notification": "make a notification gui",
    "hud": "make a hud with a health bar",
}


def _walk(n):
    yield n
    for c in n.get("children", []):
        yield from _walk(c)


@pytest.mark.parametrize("kind,msg", list(KIND_PROMPTS.items()))
def test_every_kind_is_an_instance_tree_with_no_scripts(kind, msg):
    r = route(msg)
    assert r.name == "gui", (msg, r.name, r.scores)
    out, ver = run_tool("gui", r.params, msg, {})
    assert ver and all(v["ok"] for v in ver), ver
    assert [a["type"] for a in out.actions] == ["create_instances"]
    a = out.actions[0]
    assert a["parent_path"] == "StarterGui" and a["destructive"] is False
    tree = a["tree"]
    assert tree["className"] == "ScreenGui" and validate_tree(tree) == []
    classes = {n["className"] for n in _walk(tree)}
    assert classes <= gui.GUI_CLASSES
    assert not classes & {"Script", "LocalScript", "ModuleScript"}
    _, spec = gui.design(msg)
    assert spec["kind"] == kind


@pytest.mark.parametrize("msg,kind,labels", [
    ("make a main menu with play, settings and quit buttons", "menu", ["Play", "Settings", "Quit"]),
    ("fais un menu avec des boutons jouer, options et quitter", "menu", ["Jouer", "Options", "Quitter"]),
    ("haz un menú con botones jugar, tienda y salir", "menu", ["Jugar", "Tienda", "Salir"]),
    ("make a shop with sword and shield items", "shop", ["Sword", "Shield"]),
    ("crea una tienda que vende espadas y escudos", "shop", ["Espadas", "Escudos"]),
])
def test_labels_and_kind_parsed_in_three_languages(msg, kind, labels):
    _, spec = gui.design(msg)
    assert spec["kind"] == kind and spec["labels"] == labels


def test_french_and_spanish_route_to_gui():
    for m in ("crée une interface de boutique", "fais un écran de chargement", "haz una interfaz de inventario",
              "crea una barra de vida"):
        assert route(m).name == "gui", m


def test_scripted_hud_still_routes_to_the_script_tool():
    assert route("make a working hud script").name == "ui_hud"
    assert route("make a door that opens").name == "tween_door"


def test_gui_requests_never_call_the_model():
    assert "gui" not in MODEL_FIRST
    m = _FakeModel(ok=True)
    out = GodmodeService(m, Retriever([])).chat({"message": "make a main menu with play and quit buttons"})
    assert m.calls == 0 and out["source"].startswith("tool:")
    assert all(a["type"] == "create_instances" for a in out["actions"])
