"""Roblox security and reliability lint rules over the Luau token stream.

Rules are deliberately conservative heuristics. Each has a stable ID so GRIB
tasks and the data filter can reference them. False positives are possible;
every rule has a test in tests/test_luau.py documenting its intended scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexer import LuauSyntaxError, Token, expression_if_indices, tokenize

VALIDATORS = frozenset({"typeof", "type", "assert", "tonumber", "tostring", "IsA", "clamp", "isfinite"})
DEPRECATED_GLOBALS = {"wait": "task.wait", "spawn": "task.spawn", "delay": "task.delay"}
DATASTORE_CALLS = frozenset({"GetAsync", "SetAsync", "UpdateAsync", "IncrementAsync", "RemoveAsync"})


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # "error" | "warning"
    line: int
    message: str


def _block_depth_aware_end(tokens: list[Token], fn_index: int, expr_ifs: set[int]) -> int:
    depth = 0
    pending_loop_do = 0
    for j in range(fn_index, len(tokens)):
        t = tokens[j]
        if t.kind != "keyword":
            continue
        v = t.value
        if v in ("while", "for"):
            depth += 1
            pending_loop_do += 1
        elif v == "do":
            if pending_loop_do:
                pending_loop_do -= 1
            else:
                depth += 1
        elif v == "function" or (v == "if" and j not in expr_ifs):
            depth += 1
        elif v == "end":
            depth -= 1
            if depth == 0:
                return j
    return len(tokens) - 1


def _remote_handlers(tokens: list[Token]):
    """Yield (event_name, fn_index, param_names, end_index) for remote handlers."""
    expr_ifs = expression_if_indices(tokens)
    for i, t in enumerate(tokens):
        if t.kind != "name" or t.value not in ("OnServerEvent", "OnServerInvoke"):
            continue
        # Pattern A: X.OnServerEvent:Connect(function(params) ... end)
        # Pattern B: X.OnServerInvoke = function(params) ... end
        j = i + 1
        while j < len(tokens) and j < i + 6 and not (tokens[j].kind == "keyword" and tokens[j].value == "function"):
            j += 1
        if j >= len(tokens) or tokens[j].value != "function":
            continue
        params: list[str] = []
        k = j + 2  # skip `function (`
        while k < len(tokens) and tokens[k].value != ")":
            if tokens[k].kind == "name" and tokens[k - 1].value != ":":
                params.append(tokens[k].value)
            k += 1
        yield t.value, j, params, _block_depth_aware_end(tokens, j, expr_ifs)


def lint(src: str) -> list[Finding]:
    try:
        tokens = tokenize(src)
    except LuauSyntaxError as e:
        return [Finding("GM000", "error", e.line, f"lexical error: {e}")]

    findings: list[Finding] = []
    expr_ifs = expression_if_indices(tokens)

    # Local functions whose body calls pcall count as retry/protection wrappers.
    protectors = {"pcall", "xpcall"}
    for i, t in enumerate(tokens):
        if t.kind == "keyword" and t.value == "function" and i + 1 < len(tokens) and tokens[i + 1].kind == "name":
            end = _block_depth_aware_end(tokens, i, expr_ifs)
            if any(b.value in ("pcall", "xpcall") for b in tokens[i:end]):
                protectors.add(tokens[i + 1].value)

    for i, t in enumerate(tokens):
        if t.kind != "name":
            continue
        prev = tokens[i - 1].value if i else ""
        nxt = tokens[i + 1].value if i + 1 < len(tokens) else ""
        if t.value == "loadstring" and nxt == "(":
            findings.append(Finding("GM001", "error", t.line, "loadstring executes arbitrary code; never use it on untrusted input"))
        elif t.value in ("getfenv", "setfenv") and nxt == "(":
            findings.append(Finding("GM005", "warning", t.line, f"{t.value} disables Luau optimizations"))
        elif t.value in DEPRECATED_GLOBALS and nxt == "(" and prev not in (".", ":"):
            findings.append(Finding("GM006", "warning", t.line, f"'{t.value}' is deprecated; use {DEPRECATED_GLOBALS[t.value]}"))
        elif t.value in DATASTORE_CALLS and prev == ":":
            window = tokens[max(0, i - 12) : i]
            if not any(w.kind == "name" and w.value in protectors for w in window):
                findings.append(Finding("GM007", "warning", t.line, f"DataStore {t.value} can throw; wrap it in pcall (or a pcall-based retry helper)"))

    for event, fn_i, params, end_i in _remote_handlers(tokens):
        client_args = params[1:]  # first param is the Player, supplied by the engine
        if not client_args:
            continue
        body = tokens[fn_i:end_i]
        if not any(b.kind == "name" and b.value in VALIDATORS for b in body):
            findings.append(
                Finding(
                    "GM002",
                    "error",
                    tokens[fn_i].line,
                    f"{event} handler uses client arguments {client_args} without type/range validation",
                )
            )

    return sorted(findings, key=lambda f: (f.line, f.rule))
