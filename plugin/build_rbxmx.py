#!/usr/bin/env python3
"""Package plugin/src/*.luau into a Studio-loadable plugin model (stdlib only).

Output: plugin/dist/GodmodePlugin.rbxmx (Roblox XML model format, version 4)

    Script "GodmodePlugin"          <- src/Main.luau
        ModuleScript "ActionApplier"  <- src/ActionApplier.luau
        ModuleScript "ChatUI"         <- src/ChatUI.luau
        ...

Main requires its modules with `require(script.ModuleName)`; modules require
siblings with `require(script.Parent.ModuleName)`, which works because all modules
are children of the main Script.

Usage: python3 plugin/build_rbxmx.py [--src DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_SRC = PLUGIN_DIR / "src"
DEFAULT_OUT = PLUGIN_DIR / "dist" / "GodmodePlugin.rbxmx"
MAIN_MODULE = "Main"
PLUGIN_NAME = "GodmodePlugin"

# Characters that are not allowed anywhere in an XML 1.0 document.
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def cdata(text: str) -> str:
    """Wrap text in CDATA, splitting any ']]>' so it cannot terminate the section early."""
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def referent(name: str, used: set[str]) -> str:
    """Deterministic, unique referent id ("RBX" + 32 hex digits) derived from the name."""
    salt = 0
    while True:
        digest = hashlib.sha256(f"godmode-plugin:{name}:{salt}".encode()).hexdigest()[:32].upper()
        ref = "RBX" + digest
        if ref not in used:
            used.add(ref)
            return ref
        salt += 1


def normalise_source(text: str) -> str:
    # XML parsers normalise CR/CRLF to LF, so do it up front to keep round-trips exact.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load_sources(src_dir: Path) -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in sorted(src_dir.glob("*.luau")):
        name = path.stem
        if not _MODULE_NAME.match(name):
            raise SystemExit(f"error: {path.name}: module names must be valid Luau identifiers")
        text = normalise_source(path.read_text(encoding="utf-8"))
        bad = _INVALID_XML.search(text)
        if bad:
            raise SystemExit(f"error: {path.name}: contains a character not allowed in XML: {bad.group()!r}")
        sources[name] = text
    if MAIN_MODULE not in sources:
        raise SystemExit(f"error: {src_dir}/{MAIN_MODULE}.luau is required")
    return sources


def item_xml(class_name: str, name: str, source: str, ref: str, children: list[str], indent: str) -> str:
    inner = indent + "  "
    props = [
        f'{inner}<Properties>',
        f'{inner}  <string name="Name">{escape(name)}</string>',
    ]
    if class_name == "Script":
        props.append(f'{inner}  <bool name="Disabled">false</bool>')
    props.append(f'{inner}  <ProtectedString name="Source">{cdata(source)}</ProtectedString>')
    props.append(f"{inner}</Properties>")
    body = "\n".join(props)
    kids = "".join("\n" + child for child in children)
    return f'{indent}<Item class="{class_name}" referent="{ref}">\n{body}{kids}\n{indent}</Item>'


def build(src_dir: Path = DEFAULT_SRC) -> str:
    sources = load_sources(src_dir)
    used: set[str] = set()
    main_ref = referent(PLUGIN_NAME, used)
    children = [
        item_xml("ModuleScript", name, text, referent(name, used), [], "    ")
        for name, text in sources.items()
        if name != MAIN_MODULE
    ]
    main = item_xml("Script", PLUGIN_NAME, sources[MAIN_MODULE], main_ref, children, "  ")
    return (
        '<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="http://www.roblox.com/roblox.xsd" version="4">\n'
        f"{main}\n"
        "</roblox>\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    xml = build(args.src)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(xml, encoding="utf-8", newline="\n")
    modules = len(list(args.src.glob("*.luau"))) - 1
    print(f"wrote {args.out} ({len(xml.encode('utf-8'))} bytes, Script {PLUGIN_NAME} + {modules} ModuleScripts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
