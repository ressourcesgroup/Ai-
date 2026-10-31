"""Request handling for the GODMODE server, independent of HTTP (easy to test).

Every reply names the component that produced it and only PROPOSES actions.
"""

from __future__ import annotations

import json

from godmode.anim import list_styles
from godmode.tools import TOOL_TAGS, TOOLS, ToolError, route, run_tool, verify_source

from .model_backend import ModelBackend
from .retrieval import Retriever

VERSION = "0.1.0"
PROTOCOL_VERSION = 1
MAX_MESSAGE_CHARS = 8000
MAX_SOURCE_CHARS = 400_000


class BadRequest(ValueError):
    pass


def _autonomy(v) -> int:
    if v is None:
        return 2
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not 1 <= v <= 5:
        raise BadRequest("autonomy must be an integer 1-5")
    return int(v)


class GodmodeService:
    def __init__(self, model: ModelBackend | None = None, retriever: Retriever | None = None):
        self.model = model or ModelBackend()
        self.retriever = retriever or Retriever()

    # ------------------------------------------------------------ endpoints

    def health(self) -> dict:
        return {"ok": True, "version": VERSION, "protocol_version": PROTOCOL_VERSION, "model": self.model.status(),
                "components": {
                    "router": "keyword/regex intent classifier (not the neural model)",
                    "tools": sorted(TOOL_TAGS.values()),
                    "animation_styles": [s["name"] for s in list_styles()],
                    "retrieval": self.retriever.status(),
                    "model": self.model.tag,
                }}

    def verify(self, body: dict) -> dict:
        src = body.get("source")
        if not isinstance(src, str):
            raise BadRequest("'source' must be a string")
        if len(src) > MAX_SOURCE_CHARS:
            raise BadRequest("source too long")
        from godmode.luau import check_structure, lint

        res = check_structure(src)
        findings = lint(src)
        checks = verify_source(src, "source")
        for c in checks:
            c["check"] = c["check"].split(":")[0]
        return {"ok": True, "source": "verifier", "structure": {"ok": res.ok, "errors": res.errors},
                "lint": [{"rule": f.rule, "severity": f.severity, "line": f.line, "message": f.message} for f in findings],
                "passed": res.ok and not findings, "verification": checks}

    def chat(self, body: dict) -> dict:
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise BadRequest("'message' must be a non-empty string")
        if len(message) > MAX_MESSAGE_CHARS:
            raise BadRequest(f"'message' longer than {MAX_MESSAGE_CHARS} characters")
        autonomy = _autonomy(body.get("autonomy"))
        context = body.get("context") or {}
        if not isinstance(context, dict):
            raise BadRequest("'context' must be an object")
        intent = route(message, context)

        if intent.name in TOOLS:
            resp = self._tool(intent, message, context)
        elif intent.name == "help":
            resp = self._help()
        elif intent.name == "question":
            resp = self._retrieval(message, intent)
        elif intent.name == "freeform_code":
            resp = self._model(message)
        else:
            resp = self._unknown()

        resp["intent"] = intent.to_json()
        return self._finalize(resp, autonomy)

    # ------------------------------------------------------------ handlers

    def _tool(self, intent, message: str, context: dict) -> dict:
        tag = f"tool:{TOOL_TAGS[intent.name]}"
        try:
            out, verification = run_tool(intent.name, intent.params, message, context)
        except ToolError as e:
            if intent.name == "animation" and "no animation style" in str(e):
                return self._styles_listing()
            verification = getattr(e, "verification", [])
            return {"source": tag, "actions": [], "verification": verification, "warnings": [str(e)],
                    "body": f"The `{tag}` generator could not produce a result: **{e}**. No code or actions are "
                            "returned." + (" See `verification` for the failing checks." if verification else "")}
        passed = sum(v["ok"] for v in verification)
        banner = (f"**Source:** `{tag}` - a deterministic template generator chosen by the keyword router "
                  f"(confidence {intent.confidence:.2f}); this is not neural-model output.")
        if verification:
            banner += f" Verification: {passed}/{len(verification)} checks passed."
        return {"source": tag, "actions": out.actions, "verification": verification, "warnings": list(out.warnings),
                "body": banner + "\n\n" + out.summary}

    def _help(self) -> dict:
        tools = ", ".join(f"`{t}`" for t in sorted(TOOL_TAGS.values()))
        styles = ", ".join(f"`{s['name']}`" for s in list_styles())
        return {"source": "router", "actions": [], "verification": [], "warnings": [], "body":
                "**Source:** `router` (fixed help text).\n\nI can propose verified scripts and builds with these "
                f"deterministic tools: {tools}.\n\nProcedural R15 animations: {styles} (add 'fast'/'slow' or "
                "'subtle'/'exaggerated').\n\nQuestions starting with how/what/why are answered with cited corpus "
                "examples (retrieval). Other code requests go to the small neural model "
                f"({self.model.tag}), whose output is only shown if it passes verification.\n\n"
                "Nothing is changed in Studio until you approve an action in the plugin."}

    def _styles_listing(self) -> dict:
        rows = "\n".join(f"- `{s['name']}` - {s['description']}" for s in list_styles())
        return {"source": "router", "actions": [], "verification": [], "warnings": [], "body":
                "**Source:** `router` (fixed text). Which animation? Available procedural R15 styles:\n\n" + rows}

    def _unknown(self) -> dict:
        return {"source": "router", "actions": [], "verification": [], "warnings": ["intent not recognised"], "body":
                "**Source:** `router`. I did not recognise a request I have a verified tool for. Try e.g. "
                "'make an obby with 10 platforms', 'add a datastore save system', 'make a wave animation', or ask "
                "'how do I use TweenService?'. Type `help` for the full list."}

    def _retrieval(self, message: str, intent) -> dict:
        hits = self.retriever.search(message, k=3)
        body = ("**Source:** `retrieval` - BM25 keyword search over the licensed Luau corpus. The snippets below are "
                "**retrieved examples written by others** (cited), not answers written by GODMODE's model, and may "
                "only partly answer the question.")
        from .retrieval import tokenize

        terms = list(dict.fromkeys(tokenize(message)))[:8]
        if not hits:
            body += "\n\nNo relevant examples were found."
        else:
            body += (f"\n\nThey were ranked by overlap with your key terms ({', '.join(terms)}). Read them for the API "
                     "calls and patterns involved, and check them against the Roblox documentation before reuse; "
                     "they have not been verified by GODMODE.")
        for n, h in enumerate(hits, 1):
            body += (f"\n\n**{n}. `{h.path}`** - {h.source} ({h.license}), relevance {h.score:.2f}\n\n"
                     f"```lua\n{h.snippet}\n```")
        tool_scores = {k: v for k, v in intent.scores.items() if k in TOOLS and v >= 0.4}
        if tool_scores:
            best = max(tool_scores, key=tool_scores.get)
            body += (f"\n\n_Tip:_ there is a verified generator for this (`tool:{TOOL_TAGS[best]}`); phrase it as a "
                     "request (e.g. 'make ...') to get code that passes the GODMODE checks.")
        return {"source": "retrieval", "actions": [], "verification": [], "warnings": [], "body": body,
                "citations": [h.to_json() for h in hits]}

    def _model(self, message: str) -> dict:
        res = self.model.generate_code(message)
        if not res.ok:
            body = (f"**Source:** `{res.source}`. {res.message} No code is returned rather than unverified code. "
                    "Try rephrasing as a request one of the verified tools covers (type `help`).")
            return {"source": res.source, "actions": [], "verification": res.verification, "warnings": [res.message],
                    "body": body}
        body = (f"**Source:** `{res.source}` - sampled from GODMODE's small neural model (best-of-{res.candidates}). "
                f"{res.message} Verification means 'plausible and lint-clean', not 'correct': review it carefully.\n\n"
                f"```lua\n{res.code}\n```\n\nProposed as an inert ModuleScript draft in `ServerStorage`.")
        action = {"type": "create_script", "description": "Model-generated draft (inert ModuleScript)",
                  "parent_path": "ServerStorage", "name": "GodmodeModelDraft", "class": "ModuleScript",
                  "source": res.code, "destructive": False}
        return {"source": res.source, "actions": [action], "verification": res.verification,
                "warnings": ["model output: small model, review before use"], "body": body}

    # ------------------------------------------------------------ common

    def _finalize(self, resp: dict, autonomy: int) -> dict:
        actions = resp.get("actions", [])
        body = resp.pop("body")
        if autonomy == 1:
            if actions:
                body += "\n\n**Autonomy level 1:** no actions are proposed; here is the content to apply by hand." \
                    + _actions_as_text(actions)
            actions = []
        else:
            for i, a in enumerate(actions, 1):
                a["id"] = f"a{i}"
                a["destructive"] = bool(a.get("destructive") or a["type"] == "delete_instance")
                a["requires_approval"] = True if (a["destructive"] or autonomy <= 3) else False
            if actions:
                body += (f"\n\n**Proposed only - nothing has been changed in Studio.** {len(actions)} action(s) await "
                         "your approval in the plugin:\n" + "\n".join(
                             f"- `{a['id']}` {a['type']}: {a.get('description', '')}"
                             + (" **(destructive)**" if a["destructive"] else "") for a in actions))
        out = {"ok": True, "reply_markdown": body, "source": resp["source"], "intent": resp.get("intent"),
               "actions": actions, "verification": resp.get("verification", []), "warnings": resp.get("warnings", []),
               "autonomy": autonomy, "applied": False}
        if "citations" in resp:
            out["citations"] = resp["citations"]
        return out


def _actions_as_text(actions: list[dict]) -> str:
    parts = []
    for a in actions:
        if a["type"] == "create_script":
            parts.append(f"\n\n`{a['class']}` **{a['parent_path']}.{a['name']}**\n\n```lua\n{a['source']}\n```")
        elif a["type"] == "edit_script":
            parts.append(f"\n\nNew source for **{a['path']}**\n\n```lua\n{a['new_source']}\n```")
        elif a["type"] == "delete_instance":
            parts.append(f"\n\nDelete **{a['path']}** (destructive).")
        elif a["type"] == "create_instances":
            parts.append(f"\n\nInstances under **{a['parent_path']}** (JSON tree):\n\n```json\n"
                         f"{json.dumps(a['tree'], indent=1)[:4000]}\n```")
        elif a["type"] == "create_animation":
            parts.append(f"\n\nAnimation **{a['name']}**: {len(a['keyframes'])} keyframes over {a['duration']} s "
                         f"(keyframe data omitted; use autonomy 2+ to let the plugin build it).")
    return "".join(parts)
