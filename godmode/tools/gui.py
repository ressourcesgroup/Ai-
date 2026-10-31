"""GUI designer: builds real ScreenGui instances (no scripts) from a description.

Everything here becomes ``create_instances`` under ``StarterGui``: a ScreenGui with Frames, TextLabels,
TextButtons, ScrollingFrames, ImageLabels and layout objects (UIListLayout / UIGridLayout / UICorner /
UIStroke / UIPadding / UIAspectRatioConstraint / UITextSizeConstraint). It appears in the Studio viewport
straight away and can be edited by hand like any other GUI. No LocalScript is generated: buttons are real
TextButtons that do nothing until something scripts them (each carries a ``GodmodeAction`` attribute
naming what it is for, so a script can find it).

The request is parsed with transparent rules (English, French, Spanish): GUI kind, button labels,
title, accent colour, theme, position and slot/item counts. This is a deterministic designer,
not the neural model.
"""

from __future__ import annotations

import re

from .base import COLORS, ToolOutput, c3, create_instances, enum, node, safe_identifier, udim, udim2, v2

# ------------------------------------------------------------------ vocabulary

KINDS: dict[str, list[str]] = {
    "menu": [r"\bmain menu\b", r"\btitle screen\b", r"\bstart (menu|screen)\b", r"\bmenu principal\b",
             r"\b[ée]cran (titre|d'accueil)\b", r"\bpantalla (de inicio|principal|de t[íi]tulo)\b", r"\bmen[uú]\b",
             r"\blobby (gui|ui|menu)\b"],
    "shop": [r"\bshop\b", r"\bstore\b", r"\bboutique\b", r"\bmagasin\b", r"\btienda\b", r"\bmarket\b",
             r"\bmarchand\b", r"\bgamepass\w*\b"],
    "inventory": [r"\binventor(y|ies)\b", r"\binventaire\b", r"\binventario\b", r"\bbackpack\b", r"\bhotbar\b",
                  r"\bsac\b", r"\bmochila\b", r"\bslots?\b", r"\bemplacements?\b", r"\bcasillas?\b"],
    "settings": [r"\bsettings\b", r"\boptions\b", r"\bparam[èe]tres\b", r"\br[ée]glages\b", r"\bajustes\b",
                 r"\bconfiguraci[óo]n\b", r"\bopciones\b"],
    "dialog": [r"\bdialog(ue)?\b", r"\bpop-?up\b", r"\bconfirm\w*\b", r"\bmodal\b", r"\bbo[îi]te de dialogue\b",
               r"\bdi[áa]logo\b", r"\bventana emergente\b", r"\bmessage box\b", r"\balert\b"],
    "loading": [r"\bloading\b", r"\bchargement\b", r"\bcarga(ndo)?\b", r"\bpantalla de carga\b"],
    "timer": [r"\btimer\b", r"\bcountdown\b", r"\bchrono\w*\b", r"\bminuteur\b", r"\bcompte [àa] rebours\b",
              r"\btemporizador\b", r"\bcron[óo]metro\b", r"\bcuenta atr[áa]s\b"],
    "notification": [r"\bnotif\w*\b", r"\btoast\b", r"\bbanner\b", r"\baviso\b", r"\bnotificaci[óo]n\b"],
    "hud": [r"\bhud\b", r"\bhealth ?bar\b", r"\bbarre de vie\b", r"\bbarra de (vida|salud)\b", r"\bhealth\b",
            r"\bcurrency\b", r"\bcoins? (label|display|counter)\b"],
}
# order decides ties: specific kinds before the generic HUD
KIND_ORDER = ["loading", "inventory", "shop", "settings", "dialog", "timer", "notification", "menu", "hud"]

GUI_WORDS = (r"\b(gui|guis|ui|screen ?gui|interface|interfaz|menu|men[uú]|panel|panneau|screen|[ée]cran|pantalla"
             r"|window|fen[êe]tre|ventana|buttons?|boutons?|botones|bot[óo]n|hud)\b")

LANG_HINTS = {
    "fr": r"\b(fais|faire|cr[ée]e|ajoute|une|un|avec|des|les|et|boutique|bouton|boutons|menu principal|inventaire"
          r"|param[èe]tres|chargement|jouer|quitter|fen[êe]tre|[ée]cran|intitul[ée]e?)\b",
    "es": r"\b(haz|hazme|crea|a[ñn]ade|una|con|los|las|y|tienda|bot[óo]n|botones|men[uú] principal|inventario"
          r"|ajustes|carga|jugar|salir|ventana|pantalla|titulad[oa]|llamad[oa])\b",
}

TEXT = {
    "en": {"menu": "My Game", "buttons": ["Play", "Settings", "Quit"], "shop": "Shop", "inventory": "Inventory",
           "settings": "Settings", "rows": ["Music", "Sound Effects", "Shadows"], "dialog": "Are you sure?",
           "dialog_msg": "Confirm this action.", "dialog_btn": ["Yes", "No"], "loading": "Loading...",
           "timer": "Time", "notif": "Notice", "notif_msg": "Something happened!", "buy": "Buy", "close": "X",
           "item": "Item", "coins": "Coins", "on": "ON", "volume": "Volume", "health": "Health", "hud_btn": ["Menu"]},
    "fr": {"menu": "Mon Jeu", "buttons": ["Jouer", "Paramètres", "Quitter"], "shop": "Boutique",
           "inventory": "Inventaire", "settings": "Paramètres", "rows": ["Musique", "Effets sonores", "Ombres"],
           "dialog": "Es-tu sûr ?", "dialog_msg": "Confirme cette action.", "dialog_btn": ["Oui", "Non"],
           "loading": "Chargement...", "timer": "Temps", "notif": "Info", "notif_msg": "Il s'est passé quelque chose !",
           "buy": "Acheter", "close": "X", "item": "Objet", "coins": "Pièces", "on": "OUI", "volume": "Volume",
           "health": "Vie", "hud_btn": ["Menu"]},
    "es": {"menu": "Mi Juego", "buttons": ["Jugar", "Ajustes", "Salir"], "shop": "Tienda", "inventory": "Inventario",
           "settings": "Ajustes", "rows": ["Música", "Efectos de sonido", "Sombras"], "dialog": "¿Estás seguro?",
           "dialog_msg": "Confirma esta acción.", "dialog_btn": ["Sí", "No"], "loading": "Cargando...",
           "timer": "Tiempo", "notif": "Aviso", "notif_msg": "¡Algo ha pasado!", "buy": "Comprar", "close": "X",
           "item": "Objeto", "coins": "Monedas", "on": "SÍ", "volume": "Volumen", "health": "Vida", "hud_btn": ["Menú"]},
}

EXTRA_COLORS = {
    "rouge": "red", "bleu": "blue", "vert": "green", "jaune": "yellow", "violet": "purple", "rose": "pink",
    "noir": "black", "blanc": "white", "gris": "gray", "doré": "gold", "or": "gold",
    "rojo": "red", "azul": "blue", "verde": "green", "amarillo": "yellow", "morado": "purple", "rosa": "pink",
    "negro": "black", "blanco": "white", "naranja": "orange", "dorado": "gold",
}

THEMES = {
    "dark": {"bg": (18, 20, 28), "panel": (30, 33, 45), "card": (44, 48, 64), "text": (240, 240, 245),
             "muted": (160, 165, 180)},
    "light": {"bg": (225, 228, 235), "panel": (245, 246, 250), "card": (225, 229, 238), "text": (25, 28, 38),
              "muted": (95, 100, 115)},
    "neon": {"bg": (8, 6, 20), "panel": (18, 12, 40), "card": (30, 20, 62), "text": (235, 245, 255),
             "muted": (150, 160, 210)},
}
THEME_WORDS = {"light": r"\b(light|white theme|bright|clair|claro)\b", "neon": r"\b(neon|n[ée]on|glow\w*|cyber\w*)\b",
               "dark": r"\b(dark|sombre|oscuro|night)\b"}
POSITIONS = {
    "top": r"\b(top|en haut|arriba)\b", "bottom": r"\b(bottom|en bas|abajo)\b",
    "left": r"\b(left|[àa] gauche|izquierda)\b", "right": r"\b(right|[àa] droite|derecha)\b",
}
GUI_CLASSES = {"ScreenGui", "Frame", "TextLabel", "TextButton", "ImageLabel", "ScrollingFrame", "UICorner",
               "UIStroke", "UIPadding", "UIListLayout", "UIGridLayout", "UIAspectRatioConstraint",
               "UITextSizeConstraint"}
FONT_TITLE, FONT_BODY = "Font.GothamBold", "Font.GothamMedium"

# ------------------------------------------------------------------ parsing


def detect_lang(text: str) -> str:
    t = text.lower()
    fr = len(re.findall(LANG_HINTS["fr"], t))
    es = len(re.findall(LANG_HINTS["es"], t))
    if max(fr, es) == 0:
        return "en"
    if re.search(r"\b(make|create|with|and|the|add)\b", t) and max(fr, es) < 2:
        return "en"
    return "fr" if fr > es else "es"


def detect_kind(text: str) -> str:
    t = text.lower()
    scores = {k: sum(1 for p in KINDS[k] if re.search(p, t)) for k in KIND_ORDER}
    best = max(KIND_ORDER, key=lambda k: (scores[k], -KIND_ORDER.index(k)))
    if scores[best]:
        return best
    return "panel"


def _clean_label(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip(" .,:;!?\"'«»“”`")).strip()
    s = re.sub(r"^(a|an|the|un|une|le|la|les|el|los|las|des)\s+", "", s, flags=re.I)
    s = re.sub(r"\s+(button|bouton|bot[óo]n)s?$", "", s, flags=re.I)
    return s[:24].strip()


def _split_list(s: str) -> list[str]:
    parts = re.split(r"\s*(?:,|/|\+|\band\b|\bet\b|\by\b|\be\b|&)\s*", s)
    out = []
    for p in parts:
        p = _clean_label(p)
        if p and len(p.split()) <= 3 and p.lower() not in ("a", "button", "buttons"):
            out.append(p[:1].upper() + p[1:])
    return out


STOP = r"(?=\s+(?:buttons?|boutons?|botones|with|avec|con|in|en|on|at|titled|called|that|qui|que)\b|[.;!?]|$)"


def parse_labels(message: str) -> list[str]:
    """Button labels: quoted strings first, then lists next to the word button."""
    quoted = re.findall(r"[\"“«]([^\"”»]{1,30})[\"”»]", message)
    title = parse_title(message)
    labels = [_clean_label(q) for q in quoted if _clean_label(q) and _clean_label(q) != title]
    if labels:
        return labels[:8]
    m = message
    pats = [
        # "play, settings and quit buttons" / "a play button"
        r"(?:with|avec|con|has|and)\s+(?:an?\s+|des\s+|los\s+|unos\s+|3\s+|two\s+|three\s+)?"
        r"([\w' ,&/+-]{2,80}?)\s+(?:buttons?|boutons?)\b",
        # "buttons for play, settings and quit" / "boutons jouer, options et quitter" / "botones jugar y salir"
        r"\b(?:buttons?|boutons?|botones|bot[óo]n)\s*(?::|for|pour|para|named|called|labeled|labelled|nomm[ée]s?|llamados?)?"
        r"\s+([\w' ,&/+-]{2,80}?)" + STOP,
    ]
    for p in pats:
        for mm in re.finditer(p, m, re.I):
            first = mm.group(1).strip().split()[0].lower() if mm.group(1).strip() else ""
            if first in LABEL_STOP and not re.search(r"[,]|\b(and|et|y)\b", mm.group(1)):
                continue
            got = [g for g in _split_list(mm.group(1)) if g.lower() not in LABEL_STOP
                   and not re.fullmatch(r"\d+", g)]
            if got:
                return got[:8]
    return []


LABEL_STOP = {"some", "few", "several", "quelques", "algunos", "unos", "des", "les", "los", "las", "the", "a", "an",
              "in", "on", "at", "en", "with", "avec", "con", "my", "mes", "mis", "big", "small", "nice", "cool",
              "round", "rounded", "red", "blue", "green", "yellow", "two", "three", "four", "deux", "trois", "dos",
              "tres", "button", "buttons", "bouton", "boutons", "botones"}


def parse_title(message: str) -> str | None:
    mm = re.search(r"\b(?:titled|title|called|named|intitul[ée]e?|appel[ée]e?|titul[ao]d[ao]|llamad[ao]|con el t[íi]tulo)"
                   r"\s+[\"“«']?([^\"”»',.;!?]{1,30}?)[\"”»']?(?=\s+(?:with|avec|con|and|et|y|that)\b|[,.;!?]|$)",
                   message, re.I)
    return _clean_label(mm.group(1)) if mm else None


def parse_count(message: str) -> int | None:
    mm = re.search(r"(\d{1,3})\s*(?:\w+\s){0,2}?(slots?|items?|cases?|emplacements?|objets?|casillas?|objetos?|"
                   r"art[íi]culos?|articles?|cells?|squares?)", message.lower())
    return int(mm.group(1)) if mm else None


def parse_gui(message: str) -> dict:
    t = message.lower()
    accent = None
    for name, rgb in COLORS.items():
        if re.search(rf"\b{name}\b", t):
            accent = rgb
            break
    if accent is None:
        for word, name in EXTRA_COLORS.items():
            if re.search(rf"\b{word}\b", t):
                accent = COLORS[name]
                break
    theme = next((k for k, p in THEME_WORDS.items() if re.search(p, t)), "dark")
    pos = next((k for k, p in POSITIONS.items() if re.search(p, t)), None)
    labels = parse_labels(message)
    if not labels:  # shop/inventory item names: "with sword, shield and potion items" / "que vende espada y escudo"
        mm = re.search(r"(?:with|selling|sells|that sells|avec|qui vend|con|que vende)\s+(?:an?\s+|des\s+|los\s+)?"
                       r"([\w' ,&/+-]{2,80}?)\s*(?:items?|objets?|objetos?|art[íi]culos?)?(?=[.;!?]|$)", message, re.I)
        if mm and re.search(r"\b(items?|objets?|objetos?|art[íi]culos?|selling|sells|vend\w*)\b", message, re.I):
            got = [g for g in _split_list(re.sub(r"\s*\b(items?|objets?|objetos?|art[íi]culos?)\b", "", mm.group(1)))
                   if g.lower() not in LABEL_STOP and not re.fullmatch(r"\d+", g)]
            labels = got[:12]
    # button names must not decide the GUI kind ("a menu with play, shop and quit buttons" is a menu)
    unlabelled = re.sub(r"[\"“«][^\"”»]{1,30}[\"”»]", " ", message)
    for lb in labels:
        unlabelled = re.sub(rf"\b{re.escape(lb)}\b", " ", unlabelled, flags=re.I)
    return {"kind": detect_kind(unlabelled), "lang": detect_lang(message), "labels": labels,
            "title": parse_title(message), "count": parse_count(message), "accent": accent or (0, 170, 255),
            "theme": theme, "position": pos}


# ------------------------------------------------------------------ building blocks


def _ident(label: str, suffix: str, used: set[str]) -> str:
    ascii_ = label.encode("ascii", "ignore").decode()
    base = safe_identifier(re.sub(r"\s+(\w)", lambda m: m.group(1).upper(), ascii_.title()), suffix) + suffix
    name, i = base, 2
    while name in used:
        name, i = f"{base}{i}", i + 1
    used.add(name)
    return name


def corner(px: int = 10, scale: float = 0.0) -> dict:
    return node("UICorner", "UICorner", {"CornerRadius": udim(scale, px)})


def stroke(rgb, thick: float = 2, transparency: float = 0.3) -> dict:
    return node("UIStroke", "UIStroke", {"Color": c3(rgb), "Thickness": thick, "Transparency": transparency,
                                         "ApplyStrokeMode": enum("ApplyStrokeMode.Border")})


def padding(scale: float = 0.04) -> dict:
    return node("UIPadding", "UIPadding", {k: udim(scale, 0) for k in
                                           ("PaddingTop", "PaddingBottom", "PaddingLeft", "PaddingRight")})


def aspect(ratio: float) -> dict:
    return node("UIAspectRatioConstraint", "UIAspectRatioConstraint", {"AspectRatio": round(ratio, 3)})


def text_limit(px: int) -> dict:
    return node("UITextSizeConstraint", "UITextSizeConstraint", {"MaxTextSize": px})


def label(name: str, text: str, size, pos, th: dict, font: str = FONT_BODY, color=None, max_px: int = 32,
          align: str | None = None, order: int | None = None) -> dict:
    props = {"Text": text, "Size": size, "Position": pos, "BackgroundTransparency": 1, "TextScaled": True,
             "Font": enum(font), "TextColor3": c3(color or th["text"])}
    if align:
        props["TextXAlignment"] = enum(f"TextXAlignment.{align}")
    if order is not None:
        props["LayoutOrder"] = order
    return node("TextLabel", name, props, [text_limit(max_px)])


def button(name: str, text: str, size, th: dict, accent, action: str, order: int = 0, pos=None,
           primary: bool = True, max_px: int = 30) -> dict:
    props = {"Text": text, "Size": size, "BackgroundColor3": c3(accent if primary else th["card"]),
             "TextColor3": c3((255, 255, 255) if primary else th["text"]), "TextScaled": True,
             "Font": enum(FONT_TITLE), "AutoButtonColor": True, "BorderSizePixel": 0, "LayoutOrder": order}
    if pos is not None:
        props["Position"] = pos
    return node("TextButton", name, props, [corner(8), stroke(accent, 2, 0.4 if primary else 0.2), text_limit(max_px)],
                attributes={"GodmodeAction": action})


def frame(name: str, size, pos, th: dict, key: str = "panel", transparency: float = 0, anchor=(0.5, 0.5),
          children=None, visible: bool = True) -> dict:
    props = {"Size": size, "Position": pos, "AnchorPoint": v2(*anchor), "BackgroundColor3": c3(th[key]),
             "BackgroundTransparency": transparency, "BorderSizePixel": 0}
    if not visible:
        props["Visible"] = False
    return node("Frame", name, props, children or [])


def list_layout(vertical: bool = True, pad: float = 0.03, align: str = "Center") -> dict:
    return node("UIListLayout", "UIListLayout", {
        "FillDirection": enum("FillDirection." + ("Vertical" if vertical else "Horizontal")),
        "Padding": udim(pad, 0), "SortOrder": enum("SortOrder.LayoutOrder"),
        "HorizontalAlignment": enum(f"HorizontalAlignment.{align}"),
        "VerticalAlignment": enum("VerticalAlignment.Center" if vertical else "VerticalAlignment.Center")})


def _anchor_for(pos: str | None, default: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
    """(AnchorPoint, Position scale) for a requested screen position."""
    table = {"top": ((0.5, 0), (0.5, 0.03)), "bottom": ((0.5, 1), (0.5, 0.97)), "left": ((0, 0.5), (0.03, 0.5)),
             "right": ((1, 0.5), (0.97, 0.5))}
    return table.get(pos or "", (default, default))  # type: ignore[return-value]


def panel_shell(name: str, title: str, size, ratio: float, th: dict, accent, spec: dict, close: bool, txt: dict,
                default_anchor=(0.5, 0.5)) -> tuple[dict, list]:
    anchor, at = _anchor_for(spec["position"], default_anchor)
    p = frame(name, size, udim2(at[0], 0, at[1], 0), th, anchor=anchor)
    kids = [aspect(ratio), corner(14), stroke(accent, 3, 0.2),
            label("Title", title, udim2(0.7, 0, 0.12, 0), udim2(0.04, 0, 0.03, 0), th, FONT_TITLE, max_px=40,
                  align="Left")]
    if close:
        kids.append(button("CloseButton", txt["close"], udim2(0.08, 0, 0.1, 0), th, (200, 60, 60), "close",
                           pos=udim2(0.9, 0, 0.03, 0)))
    p["children"] = kids
    return p, kids


# ------------------------------------------------------------------ GUI kinds


def build_menu(spec, th, accent, txt):
    labels = spec["labels"] or txt["buttons"]
    used: set[str] = set()
    btns = [button(_ident(lb, "Button", used), lb, udim2(1, 0, 0.18, 0), th, accent, lb.lower(), i,
                   primary=(i == 0)) for i, lb in enumerate(labels)]
    anchor, at = _anchor_for(spec["position"], (0.5, 0.5))
    col = frame("ButtonColumn", udim2(0.28, 0, 0.5, 0), udim2(at[0], 0, max(at[1], 0.35) if not spec["position"]
                                                                    else at[1], 0), th, transparency=1,
                anchor=anchor, children=[list_layout(pad=0.04)] + btns)
    col["properties"]["Position"] = udim2(at[0], 0, 0.62 if not spec["position"] else at[1], 0)
    bg = frame("Background", udim2(1, 0, 1, 0), udim2(0.5, 0, 0.5, 0), th, key="bg", transparency=0.15)
    title = label("Title", spec["title"] or txt["menu"], udim2(0.6, 0, 0.16, 0), udim2(0.2, 0, 0.12, 0), th,
                  FONT_TITLE, color=accent, max_px=90)
    title["properties"]["TextStrokeTransparency"] = 0.6
    bg["children"] = [title, col]
    return [bg], labels, True


def build_shop(spec, th, accent, txt):
    n = max(1, min(spec["count"] or len(spec["labels"]) or 6, 24))
    names = spec["labels"] or [f"{txt['item']} {i + 1}" for i in range(n)]
    n = len(names) if spec["labels"] else n
    p, kids = panel_shell("ShopFrame", spec["title"] or txt["shop"], udim2(0.55, 0, 0.7, 0), 1.45, th, accent, spec,
                          True, txt)
    kids.append(label("CurrencyLabel", f"0 {txt['coins']}", udim2(0.25, 0, 0.08, 0), udim2(0.62, 0, 0.05, 0), th,
                      color=(255, 210, 70), align="Right", max_px=26))
    used: set[str] = set()
    cards = []
    for i, nm in enumerate(names[:n]):
        price = 100 * (i + 1)
        cards.append(node("Frame", _ident(nm, "Card", used), {"BackgroundColor3": c3(th["card"]), "BorderSizePixel": 0,
                                                              "LayoutOrder": i}, [
            corner(10), stroke(accent, 1.5, 0.5),
            node("ImageLabel", "Icon", {"Size": udim2(0.6, 0, 0.45, 0), "Position": udim2(0.2, 0, 0.06, 0),
                                        "BackgroundColor3": c3(th["panel"]), "BackgroundTransparency": 0.3,
                                        "Image": "", "ScaleType": enum("ScaleType.Fit"), "BorderSizePixel": 0},
                 [corner(8)]),
            label("ItemName", nm, udim2(0.9, 0, 0.16, 0), udim2(0.05, 0, 0.54, 0), th, max_px=22),
            button("BuyButton", f"{txt['buy']} {price}", udim2(0.8, 0, 0.2, 0), th, accent, f"buy:{nm}",
                   pos=udim2(0.1, 0, 0.74, 0), max_px=20),
        ], attributes={"Price": price, "ItemName": nm}))
    kids.append(node("ScrollingFrame", "Items", {
        "Size": udim2(0.92, 0, 0.78, 0), "Position": udim2(0.04, 0, 0.18, 0), "BackgroundTransparency": 1,
        "BorderSizePixel": 0, "ScrollBarThickness": 6, "CanvasSize": udim2(0, 0, 0, 0),
        "AutomaticCanvasSize": enum("AutomaticSize.Y"), "ScrollingDirection": enum("ScrollingDirection.Y")},
        [node("UIGridLayout", "UIGridLayout", {"CellSize": udim2(0.3, 0, 0.45, 0), "CellPadding": udim2(0.03, 0, 0.03, 0),
                                               "SortOrder": enum("SortOrder.LayoutOrder")},
              [aspect(0.8)]), padding(0.01)] + cards))
    return [p], [f"{txt['buy']} ({len(cards)})", txt["close"]], False


def build_inventory(spec, th, accent, txt):
    n = max(1, min(spec["count"] or 20, 60))
    p, kids = panel_shell("InventoryFrame", spec["title"] or txt["inventory"], udim2(0.5, 0, 0.65, 0), 1.3, th,
                          accent, spec, True, txt)
    slots = [node("TextButton", f"Slot{i + 1}", {"Text": "", "BackgroundColor3": c3(th["card"]), "BorderSizePixel": 0,
                                                "AutoButtonColor": True, "LayoutOrder": i}, [
        corner(8), stroke(accent, 1.5, 0.6),
        node("ImageLabel", "Icon", {"Size": udim2(0.8, 0, 0.8, 0), "Position": udim2(0.1, 0, 0.1, 0),
                                    "BackgroundTransparency": 1, "Image": "", "ScaleType": enum("ScaleType.Fit")}),
        label("Count", "", udim2(0.5, 0, 0.3, 0), udim2(0.48, 0, 0.68, 0), th, color=th["muted"], align="Right",
              max_px=16),
    ], attributes={"GodmodeAction": f"slot:{i + 1}", "SlotIndex": i + 1}) for i in range(n)]
    kids.append(node("ScrollingFrame", "Slots", {
        "Size": udim2(0.92, 0, 0.8, 0), "Position": udim2(0.04, 0, 0.17, 0), "BackgroundTransparency": 1,
        "BorderSizePixel": 0, "ScrollBarThickness": 6, "CanvasSize": udim2(0, 0, 0, 0),
        "AutomaticCanvasSize": enum("AutomaticSize.Y"), "ScrollingDirection": enum("ScrollingDirection.Y")},
        [node("UIGridLayout", "UIGridLayout", {"CellSize": udim2(0.18, 0, 0.18, 0), "CellPadding": udim2(0.02, 0, 0.02, 0),
                                               "SortOrder": enum("SortOrder.LayoutOrder")}, [aspect(1)]),
         padding(0.01)] + slots))
    return [p], [f"{n} slots", txt["close"]], False


def build_settings(spec, th, accent, txt):
    rows_l = spec["labels"] or txt["rows"]
    p, kids = panel_shell("SettingsFrame", spec["title"] or txt["settings"], udim2(0.42, 0, 0.6, 0), 1.1, th, accent,
                          spec, True, txt)
    used: set[str] = set()
    rows = []
    for i, lb in enumerate(rows_l):
        rows.append(frame(_ident(lb, "Row", used), udim2(1, 0, 0.14, 0), udim2(0, 0, 0, 0), th, key="card",
                          anchor=(0, 0), children=[
            corner(8), label("Label", lb, udim2(0.6, 0, 0.7, 0), udim2(0.05, 0, 0.15, 0), th, align="Left", max_px=24),
            button("Toggle", txt["on"], udim2(0.22, 0, 0.7, 0), th, accent, f"toggle:{lb.lower()}",
                   pos=udim2(0.73, 0, 0.15, 0), max_px=20)]))
        rows[-1]["properties"]["LayoutOrder"] = i
    rows.append(frame("VolumeRow", udim2(1, 0, 0.14, 0), udim2(0, 0, 0, 0), th, key="card", anchor=(0, 0), children=[
        corner(8), label("Label", txt["volume"], udim2(0.3, 0, 0.7, 0), udim2(0.05, 0, 0.15, 0), th, align="Left",
                         max_px=24),
        frame("Track", udim2(0.55, 0, 0.16, 0), udim2(0.38, 0, 0.42, 0), th, key="panel", anchor=(0, 0), children=[
            corner(0, 0.5),
            node("Frame", "Fill", {"Size": udim2(0.7, 0, 1, 0), "BackgroundColor3": c3(accent), "BorderSizePixel": 0},
                 [corner(0, 0.5)]),
            node("TextButton", "Knob", {"Text": "", "Size": udim2(0, 18, 0, 18), "AnchorPoint": v2(0.5, 0.5),
                                        "Position": udim2(0.7, 0, 0.5, 0), "BackgroundColor3": c3((255, 255, 255)),
                                        "BorderSizePixel": 0, "AutoButtonColor": False},
                 [corner(0, 0.5)], attributes={"GodmodeAction": "slider:volume"})])]))
    rows[-1]["properties"]["LayoutOrder"] = len(rows_l)
    kids.append(frame("Rows", udim2(0.92, 0, 0.78, 0), udim2(0.04, 0, 0.18, 0), th, transparency=1, anchor=(0, 0),
                      children=[list_layout(pad=0.03)] + rows))
    return [p], [f"toggle:{r}" for r in rows_l] + ["slider:volume", txt["close"]], False


def build_dialog(spec, th, accent, txt):
    labels = spec["labels"] or txt["dialog_btn"]
    p, kids = panel_shell("DialogFrame", spec["title"] or txt["dialog"], udim2(0.36, 0, 0.32, 0), 1.8, th, accent,
                          spec, False, txt)
    kids[3]["properties"]["Size"] = udim2(0.92, 0, 0.2, 0)
    kids.append(label("Message", txt["dialog_msg"], udim2(0.92, 0, 0.3, 0), udim2(0.04, 0, 0.28, 0), th,
                      color=th["muted"], max_px=24))
    used: set[str] = set()
    btns = [button(_ident(lb, "Button", used), lb, udim2(0.4 if len(labels) <= 2 else 0.28, 0, 0.8, 0), th, accent,
                   lb.lower(), i, primary=(i == 0)) for i, lb in enumerate(labels)]
    kids.append(frame("Buttons", udim2(0.92, 0, 0.25, 0), udim2(0.5, 0, 0.68, 0), th, transparency=1, anchor=(0.5, 0),
                      children=[list_layout(vertical=False, pad=0.05)] + btns))
    return [p], labels, False


def build_loading(spec, th, accent, txt):
    bg = frame("LoadingScreen", udim2(1, 0, 1, 0), udim2(0.5, 0, 0.5, 0), th, key="bg")
    bg["children"] = [
        label("Title", spec["title"] or txt["menu"], udim2(0.6, 0, 0.14, 0), udim2(0.2, 0, 0.3, 0), th, FONT_TITLE,
              color=accent, max_px=80),
        frame("BarBack", udim2(0.4, 0, 0.03, 0), udim2(0.5, 0, 0.6, 0), th, key="card", children=[
            corner(0, 0.5), node("Frame", "Fill", {"Size": udim2(0.35, 0, 1, 0), "BackgroundColor3": c3(accent),
                                                   "BorderSizePixel": 0}, [corner(0, 0.5)])]),
        label("Status", txt["loading"], udim2(0.4, 0, 0.05, 0), udim2(0.3, 0, 0.64, 0), th, color=th["muted"],
              max_px=28),
    ]
    return [bg], [], True


def build_timer(spec, th, accent, txt):
    anchor, at = _anchor_for(spec["position"] or "top", (0.5, 0.03))
    f = frame("TimerFrame", udim2(0.16, 0, 0.1, 0), udim2(at[0], 0, at[1], 0), th, anchor=anchor, children=[
        aspect(2.6), corner(12), stroke(accent, 2, 0.2),
        label("Caption", spec["title"] or txt["timer"], udim2(0.9, 0, 0.3, 0), udim2(0.05, 0, 0.05, 0), th,
              color=th["muted"], max_px=18),
        label("Time", "00:00", udim2(0.9, 0, 0.6, 0), udim2(0.05, 0, 0.35, 0), th, FONT_TITLE, color=accent, max_px=48),
    ])
    return [f], [], False


def build_notification(spec, th, accent, txt):
    anchor, at = _anchor_for(spec["position"] or "right", (0.97, 0.5))
    if not spec["position"]:
        anchor, at = (1, 0), (0.98, 0.04)
    f = frame("Notification", udim2(0.26, 0, 0.12, 0), udim2(at[0], 0, at[1], 0), th, anchor=anchor, children=[
        aspect(3.4), corner(12), stroke(accent, 2, 0.1),
        node("Frame", "AccentBar", {"Size": udim2(0.025, 0, 1, 0), "BackgroundColor3": c3(accent), "BorderSizePixel": 0},
             [corner(6)]),
        label("Title", spec["title"] or txt["notif"], udim2(0.85, 0, 0.38, 0), udim2(0.08, 0, 0.08, 0), th, FONT_TITLE,
              align="Left", max_px=26),
        label("Message", txt["notif_msg"], udim2(0.85, 0, 0.4, 0), udim2(0.08, 0, 0.52, 0), th, color=th["muted"],
              align="Left", max_px=20),
    ])
    return [f], [], False


def build_hud(spec, th, accent, txt):
    used: set[str] = set()
    labels = spec["labels"] or txt["hud_btn"]
    health = frame("HealthBar", udim2(0.25, 0, 0.045, 0), udim2(0.02, 0, 0.95, 0), th, key="card", anchor=(0, 1),
                   children=[corner(0, 0.5), stroke((0, 0, 0), 2, 0.5),
                             node("Frame", "Fill", {"Size": udim2(1, 0, 1, 0), "BackgroundColor3": c3((80, 200, 90)),
                                                    "BorderSizePixel": 0}, [corner(0, 0.5)]),
                             label("Label", txt["health"], udim2(1, 0, 1, 0), udim2(0, 0, 0, 0), th, FONT_TITLE,
                                   max_px=20)])
    currency = frame("CurrencyFrame", udim2(0.14, 0, 0.06, 0), udim2(0.98, 0, 0.03, 0), th, anchor=(1, 0), children=[
        aspect(3.2), corner(10), stroke(accent, 2, 0.3),
        label("Amount", f"0 {txt['coins']}", udim2(0.9, 0, 0.8, 0), udim2(0.05, 0, 0.1, 0), th, FONT_TITLE,
              color=(255, 210, 70), max_px=28)])
    btns = [button(_ident(lb, "Button", used), lb, udim2(0.12, 0, 1, 0), th, accent, lb.lower(), i,
                   primary=(i == 0)) for i, lb in enumerate(labels)]
    for b in btns:
        b["children"].append(aspect(2.4))
    bar = frame("ButtonBar", udim2(0.6, 0, 0.07, 0), udim2(0.02, 0, 0.5, 0), th, transparency=1, anchor=(0, 0.5),
                children=[list_layout(vertical=True, pad=0.2, align="Left")] + btns)
    bar["properties"]["Size"] = udim2(0.12, 0, 0.4, 0)
    return [health, currency, bar], labels, False


def build_panel(spec, th, accent, txt):
    labels = spec["labels"] or ["OK"]
    p, kids = panel_shell("MainFrame", spec["title"] or "Panel", udim2(0.4, 0, 0.5, 0), 1.2, th, accent, spec, True,
                          txt)
    used: set[str] = set()
    btns = [button(_ident(lb, "Button", used), lb, udim2(0.8, 0, 0.14, 0), th, accent, lb.lower(), i, primary=(i == 0))
            for i, lb in enumerate(labels)]
    kids.append(frame("Buttons", udim2(0.92, 0, 0.78, 0), udim2(0.04, 0, 0.18, 0), th, transparency=1, anchor=(0, 0),
                      children=[list_layout(pad=0.04)] + btns))
    return [p], labels, False


BUILDERS = {"menu": build_menu, "shop": build_shop, "inventory": build_inventory, "settings": build_settings,
            "dialog": build_dialog, "loading": build_loading, "timer": build_timer,
            "notification": build_notification, "hud": build_hud, "panel": build_panel}
GUI_NAMES = {"menu": "MainMenuGui", "shop": "ShopGui", "inventory": "InventoryGui", "settings": "SettingsGui",
             "dialog": "DialogGui", "loading": "LoadingGui", "timer": "TimerGui", "notification": "NotificationGui",
             "hud": "HudGui", "panel": "PanelGui"}


def _walk(n: dict):
    yield n
    for c in n.get("children", []):
        yield from _walk(c)


def design(message: str, params: dict | None = None) -> tuple[dict, dict]:
    """Return (ScreenGui tree, parsed spec)."""
    spec = parse_gui(message)
    th = THEMES[spec["theme"]]
    accent = spec["accent"]
    txt = TEXT[spec["lang"]]
    roots, labels, fullscreen = BUILDERS[spec["kind"]](spec, th, accent, txt)
    name = (params or {}).get("name") or GUI_NAMES[spec["kind"]]
    gui = node("ScreenGui", safe_identifier(name, GUI_NAMES[spec["kind"]]), {
        "ResetOnSpawn": False, "ZIndexBehavior": enum("ZIndexBehavior.Sibling"), "IgnoreGuiInset": fullscreen,
        "DisplayOrder": 10 if spec["kind"] in ("loading", "dialog", "notification") else 1}, roots,
        attributes={"GodmodeGui": spec["kind"]})
    spec["buttons"] = labels
    return gui, spec


def gui_design(params: dict, ctx: dict) -> ToolOutput:
    message = ctx.get("message") or ""
    gui, spec = design(message, params)
    nodes = list(_walk(gui))
    classes = {n["className"] for n in nodes}
    buttons = [n for n in nodes if n["className"] == "TextButton"]
    unknown = sorted(classes - GUI_CLASSES)
    text_ok = all(n["properties"].get("Text") or n["name"].startswith(("Slot", "Knob")) for n in buttons)
    extra = [
        {"check": "gui:instances-only", "ok": True, "detail": "only create_instances; no Script/LocalScript generated"},
        {"check": "gui:known-classes", "ok": not unknown,
         "detail": f"{len(classes)} GUI classes used" if not unknown else f"unknown: {unknown}"},
        {"check": "gui:scale-layout", "ok": "UIAspectRatioConstraint" in classes or spec["kind"] in ("loading", "menu"),
         "detail": "Scale sizes + aspect-ratio constraints so it fits phone, tablet and PC"},
        {"check": "gui:buttons-labelled", "ok": text_ok, "detail": f"{len(buttons)} TextButton(s)"},
    ]
    where = {"top": "top", "bottom": "bottom", "left": "left", "right": "right"}.get(spec["position"] or "", "default")
    btn_txt = ", ".join(f"**{b}**" for b in spec["buttons"]) if spec["buttons"] else "none"
    summary = (
        f"Designed a **{spec['kind']}** ScreenGui `StarterGui.{gui['name']}` ({len(nodes)} instances, "
        f"{spec['theme']} theme, accent RGB {tuple(spec['accent'])}, position: {where}, language: {spec['lang']}).\n\n"
        f"Buttons/controls: {btn_txt}.\n\n"
        "It is a real GUI made of instances (Frames, TextLabels, TextButtons, layouts), **not** a script: it shows in "
        "the Studio viewport right away and you can move, recolour or retext anything in the Properties window. "
        "Sizes use Scale with aspect-ratio constraints, so it fits phone, tablet and PC.\n\n"
        "Honest limit: the buttons are not wired to anything. Each one has a `GodmodeAction` attribute "
        "saying what it is for, so a script can find it later. "
        + ("Bars, counters and timers show placeholder values. " if spec["kind"] in ("hud", "loading", "timer", "shop")
           else "")
        + "_Deterministic GUI designer, not neural-model output._")
    return ToolOutput(summary=summary,
                      actions=[create_instances("StarterGui", gui, f"Create {gui['name']} ({spec['kind']} GUI, no scripts)")],
                      extra_verification=extra)
