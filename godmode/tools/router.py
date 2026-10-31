"""Intent router: a keyword / regex classifier with a heuristic confidence score.

This is deliberately NOT the neural model. It is a transparent rule table so
that every routing decision can be explained and tested. Scores are summed
pattern weights (plus a bonus for an explicit build/make verb) capped at 1.0;
a tool intent needs >= TOOL_THRESHOLD to fire, otherwise the message falls
through to retrieval (questions) or the model (free-form code requests, including any
"make/add/create ..." request no tool covers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from godmode.anim import match_style

from .base import parse_params

ROUTER_NAME = "keyword-regex"
TOOL_THRESHOLD = 0.5

# intent -> list of (regex, weight)
RULES: dict[str, list[tuple[str, float]]] = {
    "remote_event": [(r"\bremote ?events?\b", 0.9), (r"\bremotes?\b", 0.6), (r"\bfire ?server\b", 0.6),
                     (r"\bclient\W+(to\W+)?server\b", 0.5), (r"\bnetwork(ing)?\b", 0.3)],
    "leaderstats": [(r"\bleader ?stats\b", 0.95), (r"\bleaderboard\b", 0.8), (r"\bclassement\b", 0.8),
                    (r"\btabla de (clasificaci[óo]n|puntuaci[óo]n)\b", 0.8), (r"\b(coins?|cash|kills|wins|points) (stat|counter)s?\b", 0.5)],
    "datastore": [(r"\bdata ?stores?\b", 0.95), (r"\bsav(e|es|ing)\b.{0,30}\b(data|progress|stats|coins|player)\b", 0.8),
                  (r"\bpersist\w*\b", 0.6), (r"\bsession lock\w*\b", 0.7)],
    # scripted HUD (LocalScript that drives a health bar/currency): only when a script is explicitly asked for
    "ui_hud": [(r"\b(hud|health ?bar|gui|ui)\b.{0,30}\b(script|scripted|localscript|code|working|functional|that updates|qui marche|que funcione)\b", 1.3),
               (r"\b(script|scripted|localscript|code|working|functional|fonctionnel\w*|funcional)\b.{0,30}\b(hud|health ?bar|gui|ui)\b", 1.3)],
    # GUI designer: real ScreenGui instances, no scripts
    "gui": [(r"\b(gui|guis|ui)\b", 0.7), (r"\bscreen ?gui\b", 0.9), (r"\bhud\b", 0.9), (r"\bhealth ?bar\b", 0.8),
            (r"\b(main menu|title screen|start menu|menu principal|men[uú] principal|pantalla de inicio)\b", 0.9),
            (r"\b(menu|men[uú])\b", 0.5), (r"\b(shop|store|boutique|magasin|tienda)\b", 0.55),
            (r"\b(inventory|inventaire|inventario|hotbar|backpack)\b", 0.7),
            (r"\b(settings|param[èe]tres|ajustes|configuraci[óo]n|options|opciones)\b", 0.45),
            (r"\b(pop-?up|dialog(ue)?|modal|bo[îi]te de dialogue|di[áa]logo|ventana emergente)\b", 0.6),
            (r"\b(loading screen|[ée]cran de chargement|pantalla de carga)\b", 0.9),
            (r"\b(timer|countdown|minuteur|chrono|temporizador|cron[óo]metro)\b", 0.45),
            (r"\b(notification|toast|notificaci[óo]n)\b", 0.5), (r"\b(interface|interfaz)\b", 0.7),
            (r"\b(barre de vie|barra de (vida|salud))\b", 0.8), (r"\b(buttons?|boutons?|botones|bot[óo]n)\b", 0.4),
            (r"\b(currency|coins?) (label|display|counter)\b", 0.6), (r"\b(panel|panneau|window|fen[êe]tre|ventana)\b", 0.3)],
    "npc_patrol": [(r"\bpatrol\w*\b", 0.9), (r"\bpathfinding\w*\b", 0.8), (r"\bnpcs?\b", 0.45),
                   (r"\b(pnj|pnjs)\b", 0.45), (r"\b(patrouill\w*|patrull\w*)\b", 0.9),
                   (r"\bnpcs?\b.{0,30}\b(walk|walks|move|moves|wander|roam|follow)\b", 0.4), (r"\bwaypoints?\b", 0.4)],
    "tween_door": [(r"\bdoors?\b", 0.85), (r"\bgate\b", 0.5), (r"\bportes?\b", 0.85), (r"\bpuertas?\b", 0.85)],
    "moving_platform": [(r"\bmoving platforms?\b", 0.95), (r"\bplate-?formes? (mobiles?|qui bouge\w*)\b", 0.95),
                        (r"\bplataformas? (m[óo]vil(es)?|que se mueve)\b", 0.95), (r"\bascen[cs]eur\b", 0.8),
                        (r"\bascensor\b", 0.8), (r"\bplatforms? that moves?\b", 0.9), (r"\belevator\b", 0.8),
                        (r"\blift\b", 0.5)],
    "build_obby": [(r"\bobby\b", 0.95), (r"\bobstacle course\b", 0.95), (r"\bparcours d'obstacles\b", 0.95),
                   (r"\bcarrera de obst[áa]culos\b", 0.95), (r"\bparkour\b", 0.8), (r"\bkill ?bricks?\b", 0.25),
                   (r"\blava\b", 0.4)],
    "build_house": [(r"\bhouses?\b", 0.9), (r"\bmaisons?\b", 0.9), (r"\bcasas?\b", 0.9), (r"\b(home|cabin|hut|cottage)\b", 0.7), (r"\broom\b", 0.6), (r"\bbuilding\b", 0.5)],
    "build_tower": [(r"\btowers?\b", 0.9), (r"\btours?\b", 0.85), (r"\btorres?\b", 0.9), (r"\bskyscraper\b", 0.8)],
    "build_staircase": [(r"\bstair(s|case|way)?\b", 0.9), (r"\bescaliers?\b", 0.9), (r"\bescaleras?\b", 0.9), (r"\bsteps\b", 0.4)],
    "build_arena": [(r"\barena\b", 0.9), (r"\bbattle ?ground\b", 0.8), (r"\bmap\b", 0.6), (r"\bspawn ?points?\b", 0.5),
                    (r"\bpvp\b", 0.4)],
    "build_tree": [(r"\btrees?\b", 0.85), (r"\barbres?\b", 0.85), (r"\b[áa]rbol(es)?\b", 0.85), (r"\bforest\b", 0.8), (r"\blow.?poly\b", 0.2)],
    "build_car": [(r"\bcars?\b", 0.85), (r"\bvoitures?\b", 0.85), (r"\bcoches?\b", 0.85), (r"\bvehicle\b", 0.7), (r"\btruck\b", 0.6)],
    "delete_selected": [(r"\b(delete|remove|destroy)\b", 0.5), (r"\b(selected|selection|these|this)\b", 0.3)],
    "script_review": [(r"\b(review|lint|check|audit|verify|fix)\b.{0,25}\b(script|code|my)\b", 0.9),
                      (r"\bwhat'?s wrong\b", 0.6), (r"\bopen script\b", 0.4)],
    "help": [(r"^\s*(help|commands)\s*[?!.]*\s*$", 1.0), (r"\bwhat can you do\b", 1.0), (r"\blist (your )?(tools|commands)\b", 0.9)],
}

ANIM_WORDS = r"\b(animat\w*|anim|emote|keyframes?|moves?et)\b"
# English + French + Spanish (the three trained prompt languages); accents optional.
BUILD_VERBS = (r"\b(make|build|create|generate|add|spawn|give me|i want|i need|place|put|set ?up|insert|design"
               r"|fais|faire|fait|cr[ée]e[rz]?|construi[st]|construire|ajoute[rz]?|g[ée]n[èe]re[rz]?|je veux|j'ai besoin"
               r"|mets|place[rz]?|haz|hazme|hacer|crea|crear|construye|construir|a[ñn]ade|agrega|genera|quiero|necesito"
               r"|pon|coloca)\b")
QUESTION = (r"^\s*[¿¡]?\s*(how|what|why|when|where|which|explain|is|are|does|do|should|whats|what's"
            r"|comment|pourquoi|quand|o[ùu]|quel(le)?s?|qu'est-ce|c'est quoi|est-ce|explique"
            r"|c[óo]mo|por ?qu[ée]|cu[áa]ndo|d[óo]nde|qu[ée]|cu[áa]l(es)?|explica)\b")
CODE_WORDS = (r"\b(write|code|script|function|module|class|implement|luau|lua|[ée]cri[st]|[ée]crire|fonction|programme"
              r"|escribe|escribir|c[óo]digo|funci[óo]n|programa)\b")


@dataclass
class Intent:
    name: str
    confidence: float
    params: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)
    router: str = ROUTER_NAME

    def to_json(self) -> dict:
        p = {k: v for k, v in self.params.items() if k != "rgb"}
        return {"name": self.name, "confidence": round(self.confidence, 3), "router": self.router, "params": p}


def _score(text: str, rules: list[tuple[str, float]]) -> float:
    return sum(w for pat, w in rules if re.search(pat, text))


def route(message: str, context: dict | None = None) -> Intent:
    text = (message or "").lower().strip()
    params = parse_params(message or "")
    context = context or {}
    scores: dict[str, float] = {}
    verb = bool(re.search(BUILD_VERBS, text))
    question = bool(re.match(QUESTION, text)) and not re.match(r"^\s*(can|could|would|will) you\b", text)

    for intent, rules in RULES.items():
        s = _score(text, rules)
        if s > 0 and verb and intent not in ("help", "script_review", "delete_selected"):
            s += 0.2
        scores[intent] = s

    # animation: explicit animation words and/or style keywords
    style, hits = match_style(text)
    anim = 0.0
    if re.search(ANIM_WORDS, text):
        anim += 0.6
    if hits:
        anim += 0.25 + 0.15 * min(hits, 2)
        if re.search(r"\b(character|avatar|player|my guy|rig|r15)\b", text):
            anim += 0.2
        if re.search(r"\b(play|do|does|perform)\b", text):
            anim += 0.1
    if anim:
        params["style"] = style
        scores["animation"] = anim

    if not context.get("openScript"):
        scores["script_review"] = scores.get("script_review", 0) * 0.5
    if not context.get("selection"):
        scores["delete_selected"] = min(scores.get("delete_selected", 0), 0.45)
    if scores.get("help", 0) >= 1.0:
        return Intent("help", 1.0, params, scores)

    best = max(scores, key=lambda k: scores[k])
    best_score = scores[best]
    # "how do I save data?" is a question, unless a tool keyword is overwhelming and a verb asks for it.
    if question and not (verb and best_score >= 1.1):
        conf = 0.6 if best_score < TOOL_THRESHOLD else 0.5
        return Intent("question", conf, params, scores)
    if best_score >= TOOL_THRESHOLD:
        return Intent(best, min(1.0, best_score), params, scores)
    if re.search(CODE_WORDS, text) or verb:
        # no verified tool fits, but it asks for something to be made: the neural model writes it
        return Intent("freeform_code", 0.5, params, scores)
    if question or text.endswith("?"):
        return Intent("question", 0.5, params, scores)
    return Intent("unknown", 0.2, params, scores)
