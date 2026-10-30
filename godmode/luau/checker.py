"""Structural Luau checker: lexical validity plus block/bracket balance.

This catches the most common failure modes of generated code (unterminated
strings, missing `end`, stray `then`, non-Luau operators like `!=`) without a
full parser. It accepts some programs the real compiler rejects; it should
reject very few valid programs. Treat a pass as "plausible", not "compiles".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .lexer import LuauSyntaxError, Token, expression_if_indices, tokenize

_CLOSERS = {")": "(", "}": "{", "]": "["}


@dataclass
class CheckResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)


def check_structure(src: str) -> CheckResult:
    try:
        tokens = tokenize(src)
    except LuauSyntaxError as e:
        return CheckResult(False, [str(e)])

    errors: list[str] = []
    # Stack entries: (kind, line). Kinds: function, do, loop, if, repeat, ( { [
    stack: list[tuple[str, int]] = []

    expr_ifs = expression_if_indices(tokens)

    def top() -> str | None:
        return stack[-1][0] if stack else None

    for idx, tok in enumerate(tokens):
        v, ln = tok.value, tok.line
        if tok.kind == "keyword":
            if v == "if" and idx in expr_ifs:
                stack.append(("ifexpr", ln))
            elif v in ("function", "if", "repeat"):
                stack.append((v, ln))
            elif v in ("while", "for"):
                stack.append(("loop-header", ln))
            elif v == "do":
                if top() == "loop-header":
                    stack[-1] = ("loop", stack[-1][1])
                else:
                    stack.append(("do", ln))
            elif v in ("then", "elseif"):
                if top() not in ("if", "ifexpr"):
                    errors.append(f"line {ln}: '{v}' outside of an if statement")
            elif v == "else":
                if top() == "ifexpr":
                    stack.pop()  # an if-expression ends with its else-branch expression
                elif top() != "if":
                    errors.append(f"line {ln}: 'else' outside of an if statement")
            elif v == "end":
                if top() in ("function", "do", "loop", "if"):
                    stack.pop()
                else:
                    errors.append(f"line {ln}: unexpected 'end'" + (f" (open {top()!r})" if top() else ""))
            elif v == "until":
                if top() == "repeat":
                    stack.pop()
                else:
                    errors.append(f"line {ln}: 'until' without 'repeat'")
        elif tok.kind == "op":
            if v in ("(", "{", "["):
                stack.append((v, ln))
            elif v in _CLOSERS:
                if top() == _CLOSERS[v]:
                    stack.pop()
                else:
                    errors.append(f"line {ln}: unmatched '{v}'")
        if len(errors) >= 20:
            break

    for kind, ln in stack:
        expected = {"(": "')'", "{": "'}'", "[": "']'", "repeat": "'until'", "loop-header": "'do'", "ifexpr": "'else'"}.get(kind, "'end'")
        errors.append(f"line {ln}: '{kind}' is never closed (expected {expected})")

    return CheckResult(not errors, errors, tokens)
