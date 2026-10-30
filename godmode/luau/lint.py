"""Roblox security and reliability lint rules over the Luau token stream.

Rules are deliberately conservative heuristics. Each has a stable ID so GRIB
tasks and the data filter can reference them. False positives are possible;
every rule has a test in tests/test_luau.py documenting its intended scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexer import LuauSyntaxError, Token, _skip_type, expression_if_indices, tokenize

# Calls whose arguments count as validating a client value (`typeof(x)`, `assert(x)`).
VALIDATORS = frozenset({"typeof", "type", "assert", "tonumber"})
DEPRECATED_GLOBALS = {"wait": "task.wait", "spawn": "task.spawn", "delay": "task.delay"}
DATASTORE_CALLS = frozenset({"GetAsync", "SetAsync", "UpdateAsync", "IncrementAsync", "RemoveAsync"})
CONNECT_METHODS = frozenset({"Connect", "Once", "ConnectParallel"})
# Tokens around `T[x]` (or a local holding it) that make it a nil/falsy check.
_CHECK_BEFORE = frozenset({"not", "if", "elseif", "and", "or", "while"})
_CHECK_AFTER = frozenset({"==", "~=", "then", "and", "or"})


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # "error" | "warning"
    line: int
    message: str


@dataclass(frozen=True)
class _Function:
    start: int  # index of the `function` keyword
    params: list[str]
    body: int  # index of the first body token
    end: int  # index of the closing `end`


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


def _matching(tokens: list[Token], open_i: int) -> int:
    """Index of the bracket closing tokens[open_i] (or the last token)."""
    depth = 0
    for j in range(open_i, len(tokens)):
        v = tokens[j].value if tokens[j].kind == "op" else ""
        if v in ("(", "{", "["):
            depth += 1
        elif v in (")", "}", "]"):
            depth -= 1
            if depth == 0:
                return j
    return len(tokens) - 1


def _parse_function(tokens: list[Token], fn_i: int, expr_ifs: set[int]) -> _Function:
    open_i = fn_i + 1
    while open_i < len(tokens) - 1 and tokens[open_i].value != "(":
        open_i += 1
    params: list[str] = []
    j = open_i + 1
    while j < len(tokens) and (tokens[j].kind == "name" or tokens[j].value == "..."):
        params.append(tokens[j].value)
        j += 1
        if tokens[j].value == ":":
            j = _skip_type(tokens, j + 1)
        if tokens[j].value != ",":
            break
        j += 1
    return _Function(fn_i, params, _matching(tokens, open_i) + 1, _block_depth_aware_end(tokens, fn_i, expr_ifs))


def _function_names(tokens: list[Token]) -> dict[str, list[int]]:
    """Map name -> indices of `function` keywords defining it.

    Covers `local function n`, `function a.b:n`, `local n = function` and `n = function`.
    """
    defs: dict[str, list[int]] = {}
    for i, t in enumerate(tokens):
        if t.kind != "keyword" or t.value != "function":
            continue
        if i + 1 < len(tokens) and tokens[i + 1].kind == "name":
            j = i + 1
            while j + 2 < len(tokens) and tokens[j + 1].value in (".", ":") and tokens[j + 2].kind == "name":
                j += 2
            defs.setdefault(tokens[j].value, []).append(i)
        elif i >= 2 and tokens[i - 1].value == "=" and tokens[i - 2].kind == "name":
            defs.setdefault(tokens[i - 2].value, []).append(i)
    return defs


def _resolve(defs: dict[str, list[int]], name: str, before: int) -> int | None:
    """Last definition of `name` before index `before` (else the first one after)."""
    sites = defs.get(name, [])
    earlier = [s for s in sites if s < before]
    return earlier[-1] if earlier else (sites[0] if sites else None)


def _remote_handlers(tokens: list[Token], expr_ifs: set[int]):
    """Yield (event_name, event_index, _Function) for server remote handlers."""
    defs = _function_names(tokens)
    n = len(tokens)
    for i, t in enumerate(tokens):
        if t.kind != "name" or t.value not in ("OnServerEvent", "OnServerInvoke"):
            continue
        def val(k: int) -> str:
            return tokens[k].value if k < n else ""

        fn_i: int | None = None
        arg, named = -1, False
        if val(i + 1) == ":" and val(i + 2) in CONNECT_METHODS and val(i + 3) == "(":
            arg = i + 4  # X.OnServerEvent:Connect(handler)
            named = val(arg + 1) == ")"
        elif val(i + 1) == "=":
            arg = i + 2  # X.OnServerInvoke = handler
            named = val(arg + 1) in (";", "") or tokens[arg + 1].line != tokens[arg].line
        elif val(i + 1) == "(":  # function X.OnServerInvoke(player, ...)
            k = i - 1
            while k > 0 and (tokens[k].value in (".", ":") or tokens[k].kind == "name"):
                k -= 1
            if tokens[k].kind == "keyword" and tokens[k].value == "function":
                fn_i = k
        if arg >= 0 and tokens[arg].kind == "keyword" and tokens[arg].value == "function":
            fn_i = arg
        elif arg >= 0 and tokens[arg].kind == "name" and named:
            fn_i = _resolve(defs, tokens[arg].value, i)  # a named handler defined in this file
        if fn_i is not None:
            yield t.value, i, _parse_function(tokens, fn_i, expr_ifs)


def _enclosing_calls(tokens: list[Token], k: int, lo: int) -> list[tuple[str, int, int]]:
    """(callee, open_paren, arg_position) for each call whose argument list contains k."""
    calls: list[tuple[str, int, int]] = []
    depth, pos = 0, 0
    for j in range(k - 1, lo - 1, -1):
        v = tokens[j].value if tokens[j].kind == "op" else ""
        if v in (")", "}", "]"):
            depth += 1
        elif v in ("(", "{", "["):
            if depth:
                depth -= 1
                continue
            if v == "(" and j > 0 and tokens[j - 1].kind == "name":
                calls.append((tokens[j - 1].value, j, pos))
            pos = -1  # positions are only tracked for the innermost bracket
        elif v == "," and depth == 0 and pos >= 0:
            pos += 1
    return calls


def _is_nil_checked(tokens: list[Token], start: int, stop: int, lo: int, hi: int) -> bool:
    """Is the expression tokens[start..stop] tested for nil/falsiness?"""
    before = tokens[start - 1].value if start > lo else ""
    after = tokens[stop + 1].value if stop + 1 < hi else ""
    if before in _CHECK_BEFORE or after in _CHECK_AFTER:
        return True
    # `local v = T[x]` followed by a check of `v`.
    if before == "=" and start - 3 >= lo and tokens[start - 2].kind == "name" and tokens[start - 3].value == "local":
        held = tokens[start - 2].value
        return any(tokens[j].value == held and _is_nil_checked(tokens, j, j, lo, hi) for j in range(stop + 1, hi))
    return False


class _Validation:
    def __init__(self, tokens: list[Token], defs: dict[str, list[int]], expr_ifs: set[int]):
        self.tokens, self.defs, self.expr_ifs = tokens, defs, expr_ifs

    def occurrences(self, fn: _Function, name: str) -> list[int]:
        ts = self.tokens
        return [
            k
            for k in range(fn.body, fn.end)
            if ts[k].value == name and ts[k].kind in ("name", "op") and ts[k - 1].value not in (".", ":", "::")
        ]

    def validated_at(self, fn: _Function, k: int, depth: int) -> bool:
        ts = self.tokens
        if ts[k + 1].value == ":" and ts[k + 2].value == "IsA":
            return True
        calls = _enclosing_calls(ts, k, fn.body)
        if any(callee in VALIDATORS for callee, _, _ in calls):
            return True
        if ts[k - 1].value == "[" and ts[k + 1].value == "]" and (ts[k - 2].kind == "name" or ts[k - 2].value in ("]", ")")):
            start = k - 2
            while start - 2 >= fn.body and ts[start - 1].value == "." and ts[start - 2].kind == "name":
                start -= 2
            if _is_nil_checked(ts, start, k + 1, fn.body, fn.end):
                return True
        # Passed whole to a local helper that validates that parameter.
        if depth < 3 and calls and ts[k - 1].value in ("(", ",") and ts[k + 1].value in (")", ","):
            callee, open_i, pos = calls[0]
            helper = _resolve(self.defs, callee, open_i)
            plain_call = ts[open_i - 2].value not in (".", ":")
            if helper is not None and helper != fn.start and pos >= 0 and plain_call:
                hf = _parse_function(ts, helper, self.expr_ifs)
                if pos < len(hf.params) and self.unvalidated(hf, [hf.params[pos]], depth + 1) == []:
                    return True
        return False

    def unvalidated(self, fn: _Function, names: list[str], depth: int = 0) -> list[str]:
        ts = self.tokens
        bad: list[str] = []
        for name in names:
            if name.startswith("_"):
                continue
            uses = self.occurrences(fn, name)
            if name != "...":
                if uses and not any(self.validated_at(fn, k, depth) for k in uses):
                    bad.append(name)
                continue
            # Varargs: `local a, b = ...` names them; `select("#", ...)` only counts them.
            for k in uses:
                if ts[k - 1].value == "=":  # `local a, b = ...`
                    j, unpacked = k - 2, []
                    while j > fn.body and ts[j].kind == "name":
                        unpacked.insert(0, ts[j].value)
                        if ts[j - 1].value != ",":
                            break
                        j -= 2
                    if unpacked and ts[j - 1].value == "local":
                        client = unpacked[1:] if fn.params[:1] == ["..."] else unpacked
                        bad += self.unvalidated(fn, client, depth)
                        continue
                calls = _enclosing_calls(ts, k, fn.body)
                if calls and calls[0][0] == "select" and ts[calls[0][1] + 1].value in ('"#"', "'#'"):
                    continue
                if not self.validated_at(fn, k, depth) and "..." not in bad:
                    bad.append("...")
        return bad


def _bound_names(tokens: list[Token]) -> set[str]:
    """Names the script binds itself: locals, `function name`, and parameters."""
    bound: set[str] = set()
    for i, t in enumerate(tokens):
        if t.kind != "keyword":
            continue
        if t.value == "local":
            j = i + 1
            if j < len(tokens) and tokens[j].value == "function":
                j += 1
            while j < len(tokens) and tokens[j].kind == "name":
                bound.add(tokens[j].value)
                j += 1
                if tokens[j].value == ":":
                    j = _skip_type(tokens, j + 1)
                if tokens[j].value != ",":
                    break
                j += 1
        elif t.value == "function" and i + 2 < len(tokens) and tokens[i + 1].kind == "name" and tokens[i + 2].value in ("(", "<"):
            bound.add(tokens[i + 1].value)
    return bound


def lint(src: str) -> list[Finding]:
    try:
        tokens = tokenize(src)
    except LuauSyntaxError as e:
        return [Finding("GM000", "error", e.line, f"lexical error: {e}")]

    findings: list[Finding] = []
    expr_ifs = expression_if_indices(tokens)
    defs = _function_names(tokens)
    bound = _bound_names(tokens)
    n = len(tokens)

    # Protected regions: protector bodies (functions that call pcall/xpcall) and
    # function literals passed directly to pcall/xpcall or to a protector.
    protectors = {"pcall", "xpcall"}
    regions: list[tuple[int, int]] = []
    for name, sites in defs.items():
        for fn_i in sites:
            end = _block_depth_aware_end(tokens, fn_i, expr_ifs)
            if any(b.kind == "name" and b.value in ("pcall", "xpcall") for b in tokens[fn_i:end]):
                protectors.add(name)
                regions.append((fn_i, end))
    for i, t in enumerate(tokens):
        if t.kind != "name" or t.value not in protectors or i + 1 >= n or tokens[i + 1].value != "(":
            continue
        if i and tokens[i - 1].value == "function":
            continue
        close, j, depth = _matching(tokens, i + 1), i + 2, 0
        while j < close:
            tj = tokens[j]
            if tj.kind == "keyword" and tj.value == "function" and depth == 0:
                end = _block_depth_aware_end(tokens, j, expr_ifs)
                regions.append((j, end))
                j = end
            elif tj.kind == "op" and tj.value in ("(", "{", "["):
                depth += 1
            elif tj.kind == "op" and tj.value in (")", "}", "]"):
                depth -= 1
            j += 1

    for i, t in enumerate(tokens):
        if t.kind != "name":
            continue
        prev = tokens[i - 1].value if i else ""
        nxt = tokens[i + 1].value if i + 1 < n else ""
        member = prev in (".", ":")
        if t.value == "loadstring" and not member and nxt != "=":
            findings.append(Finding("GM001", "error", t.line, "loadstring executes arbitrary code; never use it on untrusted input"))
        elif t.value in ("getfenv", "setfenv") and nxt == "(" and not member:
            findings.append(Finding("GM005", "warning", t.line, f"{t.value} disables Luau optimizations"))
            close = _matching(tokens, i + 1)
            after = tokens[close + 1] if close + 1 < n else None
            if t.value == "getfenv" and after and (after.value == "[" or (after.value == "." and tokens[close + 2].value == "loadstring")):
                findings.append(Finding("GM001", "error", t.line, "indexing the environment can reach loadstring; never do this"))
        elif t.value in DEPRECATED_GLOBALS and nxt == "(" and not member and t.value not in bound:
            findings.append(Finding("GM006", "warning", t.line, f"'{t.value}' is deprecated; use {DEPRECATED_GLOBALS[t.value]}"))
        elif t.value in DATASTORE_CALLS and prev == ":":
            if not any(lo < i < hi for lo, hi in regions):
                findings.append(Finding("GM007", "warning", t.line, f"DataStore {t.value} can throw; wrap it in pcall (or a pcall-based retry helper)"))

    validation = _Validation(tokens, defs, expr_ifs)
    for event, event_i, fn in _remote_handlers(tokens, expr_ifs):
        # The first parameter is the Player, supplied by the engine; varargs hold it too.
        client = fn.params if fn.params[:1] == ["..."] else fn.params[1:]
        bad = validation.unvalidated(fn, client)
        if bad:
            findings.append(
                Finding(
                    "GM002",
                    "error",
                    tokens[event_i].line,
                    f"{event} handler uses client arguments {bad} without type/range validation",
                )
            )

    return sorted(findings, key=lambda f: (f.line, f.rule))
