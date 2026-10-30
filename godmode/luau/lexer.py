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
            i += 1
            while True:
                if i >= n or src[i] == "\n":
                    raise LuauSyntaxError("unfinished string", start_line)
                if src[i] == "\\":
                    # `\z` and escaped newlines may legally span lines.
                    if i + 1 < n and src[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if src[i] == c:
                    i += 1
                    break
                i += 1
            emit("string", src[start:i], start_line)
            continue

        if c == "`":
            # Interpolated string: `text {expr} text`. Braces nest inside expr.
            i += 1
            depth = 0
            while True:
                if i >= n:
                    raise LuauSyntaxError("unfinished interpolated string", start_line)
                ch = src[i]
                if ch == "\\":
                    i += 2
                    continue
                if ch == "\n":
                    if depth == 0:
                        raise LuauSyntaxError("unfinished interpolated string", start_line)
                    line += 1
                elif ch == "{":
                    depth += 1
                elif ch == "}" and depth > 0:
                    depth -= 1
                elif ch == "`" and depth == 0:
                    i += 1
                    break
                i += 1
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
_EXPR_KEYWORDS = frozenset({"return", "and", "or", "not", "in", "then", "else"})
_STATEMENT_END_OPS = frozenset({")", "]", "}", ";", "..."})
# `?` and `>` can close a type annotation (`): Part?`, `): Map<K, V>`) or be an
# operator; an `if` on a later line than them starts a statement.
_AMBIGUOUS_OPS = frozenset({"?", ">"})


def expression_if_indices(tokens: list[Token]) -> set[int]:
    """Indices of `if` keywords that start Luau if-expressions.

    `local x = if a then b else c` has no `end`; statement-level `if` does.
    The previous token decides: an operator or expression keyword means an
    expression is expected. `then`/`else` count only inside an if-expression
    (e.g. `if a then if b then c else d else e`), tracked with a stack.
    """
    result: set[int] = set()
    open_expr = 0
    for i, t in enumerate(tokens):
        if t.kind != "keyword" or t.value != "if" or i == 0:
            if t.kind == "keyword" and t.value == "else" and open_expr:
                open_expr -= 1
            continue
        prev = tokens[i - 1]
        if prev.kind == "op" and prev.value in _AMBIGUOUS_OPS:
            is_expr = prev.line == t.line
        elif prev.kind == "op" and prev.value not in _STATEMENT_END_OPS:
            is_expr = True
        elif prev.kind == "keyword" and prev.value in _EXPR_KEYWORDS:
            is_expr = prev.value not in ("then", "else") or open_expr > 0
        else:
            is_expr = False
        if is_expr:
            result.add(i)
            open_expr += 1
    return result
