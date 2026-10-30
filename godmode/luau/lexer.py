"""A Luau lexer written from the Luau grammar (https://luau.org/grammar).

It is used for three things: data quality filtering, the structural checker,
and GRIB grading. It is NOT the official Luau front end; when the real
`luau-analyze` binary is available it should be preferred (see docs/STATUS.md).
"""

from __future__ import annotations

from dataclasses import dataclass

KEYWORDS = frozenset(
    {
        "and", "break", "do", "else", "elseif", "end", "false", "for",
        "function", "if", "in", "local", "nil", "not", "or", "repeat",
        "return", "then", "true", "until", "while",
    }
)

# Longest operators first so maximal munch works with a simple prefix scan.
OPERATORS = (
    "...", "..=", "//=",
    "==", "~=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "^=", "..", "::", "->", "//",
    "+", "-", "*", "/", "%", "^", "#", "<", ">", "=", "(", ")", "{", "}", "[", "]",
    ";", ":", ",", ".", "?", "|", "&", "@",
)


@dataclass(frozen=True)
class Token:
    kind: str  # name | keyword | number | string | comment | op | eof
    value: str
    line: int


class LuauSyntaxError(ValueError):
    def __init__(self, message: str, line: int):
        super().__init__(f"line {line}: {message}")
        self.line = line


def _long_bracket_level(src: str, i: int) -> int:
    """If src[i] starts `[==[`, return the number of '=', else -1."""
    if i >= len(src) or src[i] != "[":
        return -1
    j = i + 1
    while j < len(src) and src[j] == "=":
        j += 1
    if j < len(src) and src[j] == "[":
        return j - i - 1
    return -1


def _scan_escape(src: str, i: int, line: int) -> tuple[int, int]:
    """src[i] is a backslash inside a string; return the index after the escape."""
    nxt = src[i + 1] if i + 1 < len(src) else ""
    if nxt == "\n":
        return i + 2, line + 1
    if nxt == "\r":
        i += 2
        if i < len(src) and src[i] == "\n":
            i += 1
        return i, line + 1
    if nxt == "z":  # `\z` skips all following whitespace, newlines included
        i += 2
        while i < len(src) and src[i] in " \t\r\n\f\v":
            line += src[i] == "\n"
            i += 1
        return i, line
    return i + 2, line


def _scan_quoted(src: str, i: int, line: int) -> tuple[int, int]:
    """src[i] opens a '...' or "..." string; return (index after it, line)."""
    quote, start_line, n = src[i], line, len(src)
    i += 1
    while True:
        if i >= n or src[i] in "\r\n":
            raise LuauSyntaxError("unfinished string", start_line)
        if src[i] == "\\":
            i, line = _scan_escape(src, i, line)
        elif src[i] == quote:
            return i + 1, line
        else:
            i += 1


def _scan_interpolated(src: str, i: int, line: int) -> tuple[int, int]:
    """src[i] opens `text {expr} text`; expressions may hold strings and braces."""
    start_line, n = line, len(src)
    i += 1
    while True:  # text segment
        if i >= n or src[i] in "\r\n":
            raise LuauSyntaxError("unfinished interpolated string", start_line)
        ch = src[i]
        if ch == "\\":
            i, line = _scan_escape(src, i, line)
        elif ch == "`":
            return i + 1, line
        elif ch == "{":
            if src.startswith("{{", i):
                raise LuauSyntaxError("double braces are not permitted in interpolated strings; use '\\{'", line)
            i, line = _scan_interpolation_expr(src, i + 1, line, start_line)
        else:
            i += 1


def _scan_interpolation_expr(src: str, i: int, line: int, start_line: int) -> tuple[int, int]:
    """Skip an expression after `{` up to its matching `}`; return the index after `}`."""
    depth, n = 0, len(src)
    while True:
        if i >= n:
            raise LuauSyntaxError("unfinished interpolated string", start_line)
        ch = src[i]
        if ch in "\"'":
            i, line = _scan_quoted(src, i, line)
        elif ch == "`":
            i, line = _scan_interpolated(src, i, line)
        elif _long_bracket_level(src, i) >= 0:
            level = _long_bracket_level(src, i)
            end = src.find("]" + "=" * level + "]", i)
            if end < 0:
                raise LuauSyntaxError("unfinished long string", line)
            line += src.count("\n", i, end)
            i = end + level + 2
        elif ch == "{":
            depth += 1
            i += 1
        elif ch == "}":
            if depth == 0:
                return i + 1, line
            depth -= 1
            i += 1
        else:
            line += ch == "\n"
            i += 1


def tokenize(src: str, keep_comments: bool = False) -> list[Token]:
    tokens: list[Token] = []
    i, n, line = 0, len(src), 1

    def emit(kind: str, value: str, start_line: int) -> None:
        if kind != "comment" or keep_comments:
            tokens.append(Token(kind, value, start_line))

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r\f\v":
            i += 1
            continue

        start, start_line = i, line

        if src.startswith("--", i):
            level = _long_bracket_level(src, i + 2)
            if level >= 0:
                close = "]" + "=" * level + "]"
                end = src.find(close, i + 4 + level)
                if end < 0:
                    raise LuauSyntaxError("unfinished long comment", start_line)
                i = end + len(close)
            else:
                end = src.find("\n", i)
                i = n if end < 0 else end
            text = src[start:i]
            line += text.count("\n")
            emit("comment", text, start_line)
            continue

        level = _long_bracket_level(src, i)
        if level >= 0:
            close = "]" + "=" * level + "]"
            end = src.find(close, i + 2 + level)
            if end < 0:
                raise LuauSyntaxError("unfinished long string", start_line)
            i = end + len(close)
            text = src[start:i]
            line += text.count("\n")
            emit("string", text, start_line)
            continue

        if c in "\"'":
            i, line = _scan_quoted(src, i, line)
            emit("string", src[start:i], start_line)
            continue

        if c == "`":
            i, line = _scan_interpolated(src, i, line)
            emit("string", src[start:i], start_line)
            continue

        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            if src.startswith(("0x", "0X", "0b", "0B"), i):
                i += 2
                while i < n and (src[i].isalnum() or src[i] == "_"):
                    i += 1
            else:
                while i < n and (src[i].isdigit() or src[i] in "._"):
                    i += 1
                if i < n and src[i] in "eE":
                    i += 1
                    if i < n and src[i] in "+-":
                        i += 1
                    while i < n and src[i].isdigit():
                        i += 1
            emit("number", src[start:i], start_line)
            continue

        if c.isalpha() or c == "_":
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            word = src[start:i]
            emit("keyword" if word in KEYWORDS else "name", word, start_line)
            continue

        for op in OPERATORS:
            if src.startswith(op, i):
                i += len(op)
                emit("op", op, start_line)
                break
        else:
            raise LuauSyntaxError(f"unexpected character {c!r}", start_line)

    tokens.append(Token("eof", "", line))
    return tokens


# Tokens after which an `if` begins an if-then-else *expression* (no `end`).
_EXPR_KEYWORDS = frozenset({"return", "and", "or", "not", "in", "if", "elseif", "while", "until"})
_STATEMENT_END_OPS = frozenset({")", "]", "}", ";", "...", "?"})
_OPENERS = {"(": ")", "{": "}", "[": "]", "<": ">"}


def _skip_balanced(tokens: list[Token], j: int) -> int:
    """tokens[j] is an opener; return the index after its matching closer."""
    stack: list[str] = []
    while j < len(tokens) and tokens[j].kind != "eof":
        v = tokens[j].value if tokens[j].kind == "op" else ""
        if v in _OPENERS:
            stack.append(_OPENERS[v])
        elif v in (")", "}", "]", ">"):
            while v != ">" and stack and stack[-1] == ">":
                stack.pop()  # a stray `<` (comparison inside typeof) never closes
            if not stack or stack[-1] != v:
                return j  # unbalanced; let the structural checker report it
            stack.pop()
            if not stack:
                return j + 1
        j += 1
    return j


def _skip_type(tokens: list[Token], j: int) -> int:
    """Return the index just after the Luau type starting at tokens[j]."""
    n = len(tokens)
    while j < n:
        t = tokens[j]
        if t.kind == "op" and t.value == "<":  # generic function type `<T>(T) -> T`
            j = _skip_balanced(tokens, j)
            continue
        if t.kind == "op" and t.value == "...":
            j += 1
            continue
        if t.kind == "op" and t.value in ("(", "{"):
            j = _skip_balanced(tokens, j)
        elif t.kind == "name" and t.value == "typeof" and j + 1 < n and tokens[j + 1].value == "(":
            j = _skip_balanced(tokens, j + 1)
        elif t.kind == "name":
            j += 1
            while j + 1 < n and tokens[j].value == "." and tokens[j + 1].kind == "name":
                j += 2
            if j < n and tokens[j].value == "<":
                j = _skip_balanced(tokens, j)
        elif t.kind in ("string", "keyword") and (t.kind == "string" or t.value in ("nil", "true", "false")):
            j += 1
        else:
            return j
        if j < n and tokens[j].value == "->":  # function type
            j += 1
            continue
        while j < n and tokens[j].value == "?":
            j += 1
        if j < n and tokens[j].kind == "op" and tokens[j].value in ("|", "&"):
            j += 1
            continue
        return j
    return j


def type_annotation_ends(tokens: list[Token]) -> set[int]:
    """Indices of the last token of each type annotation, alias, cast or return type.

    A statement may follow a type directly (`function f(): T? if ...`), so the
    token after one of these never continues an expression.
    """
    ends: set[int] = set()
    n = len(tokens)

    def typ(j: int) -> int:
        k = _skip_type(tokens, j)
        if k > j:
            ends.add(k - 1)
        return k

    for i, t in enumerate(tokens):
        v = t.value
        if t.kind == "op" and v == "::":
            typ(i + 1)
        elif t.kind == "name" and v == "type" and i + 2 < n and tokens[i + 1].kind == "name" and tokens[i + 2].value in ("=", "<"):
            j = i + 2
            if tokens[j].value == "<":
                j = _skip_balanced(tokens, j)
            if j < n and tokens[j].value == "=":
                typ(j + 1)
        elif t.kind == "keyword" and v in ("local", "for"):
            j = i + 1
            while j + 1 < n and tokens[j].kind == "name":
                if tokens[j + 1].value == ":":
                    j = typ(j + 2)
                else:
                    j += 1
                if j >= n or tokens[j].value != ",":
                    break
                j += 1
        elif t.kind == "keyword" and v == "function":
            j = i + 1
            while j < n and (tokens[j].kind == "name" or tokens[j].value in (".", ":")):
                j += 1
            if j < n and tokens[j].value == "<":
                j = _skip_balanced(tokens, j)
            if j >= n or tokens[j].value != "(":
                continue
            j += 1
            while j < n and tokens[j].value != ")":  # parameters: `name: T`, `...: T`
                if tokens[j].kind == "name" or tokens[j].value == "...":
                    j += 1
                    if j < n and tokens[j].value == ":":
                        j = typ(j + 1)
                if j < n and tokens[j].value == ",":
                    j += 1
                elif j < n and tokens[j].value != ")":
                    break
            if j + 1 < n and tokens[j].value == ")" and tokens[j + 1].value == ":":
                typ(j + 2)
    return ends


def expression_if_indices(tokens: list[Token]) -> set[int]:
    """Indices of `if` keywords that start Luau if-expressions.

    `local x = if a then b else c` has no `end`; statement-level `if` does.
    The previous token decides: an operator or expression keyword means an
    expression is expected, the end of a type annotation means a statement.
    `then`/`else` start an expression only when they belong to an
    if-expression, which a block stack tracks (`if a then 1 else if b ...`).
    """
    result: set[int] = set()
    type_ends = type_annotation_ends(tokens)
    stack: list[str] = []  # ifexpr, if, function, do, loop-header, loop, repeat
    expr_branch = -1  # index of the last `then`/`else` belonging to an if-expression
    for i, t in enumerate(tokens):
        if t.kind != "keyword":
            continue
        v = t.value
        if v == "if":
            prev = tokens[i - 1] if i else None
            if prev is None or i - 1 in type_ends:
                is_expr = False
            elif prev.kind == "op":
                is_expr = prev.value not in _STATEMENT_END_OPS and not (prev.value == ">" and prev.line != t.line)
            elif prev.kind == "keyword":
                is_expr = prev.value in _EXPR_KEYWORDS or i - 1 == expr_branch
            else:
                is_expr = False
            if is_expr:
                result.add(i)
            stack.append("ifexpr" if is_expr else "if")
        elif v in ("then", "else"):
            if stack and stack[-1] == "ifexpr":
                expr_branch = i
                if v == "else":
                    stack.pop()  # the else-branch expression has no terminator
        elif v in ("function", "repeat"):
            stack.append(v)
        elif v in ("while", "for"):
            stack.append("loop-header")
        elif v == "do":
            if stack and stack[-1] == "loop-header":
                stack[-1] = "loop"
            else:
                stack.append("do")
        elif v in ("end", "until"):
            while stack and stack[-1] == "ifexpr":
                stack.pop()  # malformed input; keep going
            if stack:
                stack.pop()
    return result
