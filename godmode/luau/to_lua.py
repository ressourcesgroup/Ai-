"""Luau -> Lua 5.4 source translation, for executing plugin modules in tests.

This is a test-harness tool, not a Luau implementation. It parses Luau with a
small recursive-descent parser (built on ``godmode.luau.lexer``) and emits Lua
5.4 that keeps the original line numbers, so runtime errors point at the real
Luau line. What it changes:

* type syntax is removed: ``type``/``export type`` declarations, annotations on
  locals, parameters and return types, generic parameter lists, ``::`` casts;
* ``a op= b`` becomes ``a = a op (b)`` (the target expression is evaluated twice,
  so it must not have side effects; the plugin's targets never do);
* ``if c then a else b`` expressions become a one-value immediately-invoked
  closure (so they cannot use ``...`` of the enclosing function);
* ``continue`` becomes ``goto`` to a label at the end of the loop body;
* ``for k, v in t do`` (generalized iteration) is routed through ``__iter``;
* ``a .. b`` becomes ``__cat(a, b)`` so numbers print like Luau (``5`` not ``5.0``);
* number literals with ``_`` separators or ``0b`` binary prefixes are rewritten.

Interpolated strings (backticks) are rejected with a clear error: the plugin
does not use them, and a half-correct translation would be worse than none.
The runtime helpers (``__iter``, ``__cat``) live in ``roblox_mock.lua``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexer import Token, _skip_balanced, _skip_type, tokenize


class TranslateError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass
class Piece:
    line: int
    text: str


BINARY_PRIORITY = {
    "+": (10, 10), "-": (10, 10),
    "*": (11, 11), "/": (11, 11), "//": (11, 11), "%": (11, 11),
    "^": (14, 13),
    "..": (9, 8),
    "==": (3, 3), "~=": (3, 3), "<": (3, 3), "<=": (3, 3), ">": (3, 3), ">=": (3, 3),
    "and": (2, 2), "or": (1, 1),
}
UNARY_PRIORITY = 12
COMPOUND = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "//=": "//", "%=": "%", "^=": "^", "..=": ".."}
BLOCK_END = {"end", "else", "elseif", "until", "eof"}


def _number(text: str) -> str:
    t = text.replace("_", "")
    if t[:2].lower() == "0b":
        return str(int(t[2:], 2))
    return t


class _Parser:
    def __init__(self, src: str):
        self.toks: list[Token] = tokenize(src)
        self.i = 0
        self.loop_labels: list[list[str | None]] = []  # per loop: continue label (lazily created)
        self.label_counter = 0

    # -- token helpers -------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Token:
        j = min(self.i + k, len(self.toks) - 1)
        return self.toks[j]

    def check(self, value: str) -> bool:
        t = self.tok
        return t.kind in ("op", "keyword") and t.value == value

    def accept(self, value: str) -> Token | None:
        if self.check(value):
            t = self.tok
            self.i += 1
            return t
        return None

    def expect(self, value: str) -> Token:
        t = self.accept(value)
        if t is None:
            raise TranslateError(f"expected {value!r} near {self.tok.value!r}", self.tok.line)
        return t

    def name(self) -> Token:
        t = self.tok
        if t.kind != "name":
            raise TranslateError(f"expected a name near {t.value!r}", t.line)
        self.i += 1
        return t

    def skip_type(self) -> None:
        if self.tok.kind == "op" and self.tok.value in ("|", "&"):  # leading `| "a" | "b"`
            self.i += 1
        j = _skip_type(self.toks, self.i)
        if j == self.i:
            raise TranslateError(f"cannot read type near {self.tok.value!r}", self.tok.line)
        self.i = j

    def skip_generics(self) -> None:
        if self.check("<"):
            self.i = _skip_balanced(self.toks, self.i)

    def is_type_statement(self) -> bool:
        t = self.tok
        if t.kind == "name" and t.value == "export" and self.peek().value == "type" and self.peek(2).kind == "name":
            return True
        return t.kind == "name" and t.value == "type" and self.peek().kind == "name" and self.peek(2).value in ("=", "<")

    # -- blocks and statements -----------------------------------------
    def block(self) -> list[Piece]:
        out: list[Piece] = []
        while not (self.tok.kind == "eof" or (self.tok.kind == "keyword" and self.tok.value in BLOCK_END)):
            if self.check("return"):
                out += self.return_stat()
                break
            out += self.statement()
        return out

    def return_stat(self) -> list[Piece]:
        t = self.expect("return")
        out = [Piece(t.line, "return")]
        if not (self.tok.kind == "eof" or self.check(";") or (self.tok.kind == "keyword" and self.tok.value in BLOCK_END)):
            out += self.exprlist()
        self.accept(";")
        return out

    def loop_body(self) -> list[Piece]:
        """Block of a loop; `continue` jumps to a label emitted at its end."""
        self.loop_labels.append([None])
        body = self.block()
        label = self.loop_labels.pop()[0]
        if label is not None:
            body.append(Piece(self.tok.line, f"::{label}::"))
        return body

    def statement(self) -> list[Piece]:
        t = self.tok
        line = t.line
        if self.accept(";"):
            # Keep explicit separators: `f(x); (y :: any).z = 1` must not become the
            # call `f(x)(y).z = 1` once the cast parentheses are emitted.
            return [Piece(line, ";")]
        if self.is_type_statement():
            if t.value == "export":
                self.i += 1
            self.i += 2  # `type Name`
            self.skip_generics()
            self.expect("=")
            self.skip_type()
            return []
        if t.kind == "name" and t.value == "continue" and self.loop_labels and not self._continues_expression():
            self.i += 1
            slot = self.loop_labels[-1]
            if slot[0] is None:
                self.label_counter += 1
                slot[0] = f"__continue{self.label_counter}"
            return [Piece(line, f"goto {slot[0]}")]
        if t.kind == "keyword":
            v = t.value
            if v == "local":
                self.i += 1
                if self.accept("function"):
                    n = self.name()
                    return [Piece(line, "local function"), Piece(n.line, n.value)] + self.funcbody()
                out = [Piece(line, "local")]
                names = []
                while True:
                    n = self.name()
                    names.append(Piece(n.line, n.value))
                    if self.check("<") and self.peek().kind == "name" and self.peek().value in ("const", "close"):
                        raise TranslateError("local attributes are not Luau", n.line)
                    if self.accept(":"):
                        self.skip_type()
                    if not self.accept(","):
                        break
                    names.append(Piece(n.line, ","))
                out += names
                if self.check("="):
                    eq = self.expect("=")
                    out.append(Piece(eq.line, "="))
                    out += self.exprlist()
                return out
            if v == "function":
                self.i += 1
                out = [Piece(line, "function")]
                n = self.name()
                out.append(Piece(n.line, n.value))
                while self.check(".") or self.check(":"):
                    sep = self.tok
                    self.i += 1
                    m = self.name()
                    out.append(Piece(sep.line, sep.value + m.value))
                return out + self.funcbody()
            if v == "if":
                self.i += 1
                out = [Piece(line, "if")] + self.expr() + [Piece(self.expect("then").line, "then")] + self.block()
                while self.check("elseif"):
                    e = self.expect("elseif")
                    out += [Piece(e.line, "elseif")] + self.expr() + [Piece(self.expect("then").line, "then")] + self.block()
                if self.check("else"):
                    e = self.expect("else")
                    out += [Piece(e.line, "else")] + self.block()
                return out + [Piece(self.expect("end").line, "end")]
            if v == "while":
                self.i += 1
                out = [Piece(line, "while")] + self.expr() + [Piece(self.expect("do").line, "do")]
                return out + self.loop_body() + [Piece(self.expect("end").line, "end")]
            if v == "do":
                self.i += 1
                return [Piece(line, "do")] + self.block() + [Piece(self.expect("end").line, "end")]
            if v == "repeat":
                self.i += 1
                out = [Piece(line, "repeat")] + self.loop_body()
                return out + [Piece(self.expect("until").line, "until")] + self.expr()
            if v == "for":
                return self.for_stat()
            if v == "break":
                self.i += 1
                return [Piece(line, "break")]
            if v == "return":
                return self.return_stat()
        return self.expr_statement()

    def _continues_expression(self) -> bool:
        """`continue` is only a keyword when it is not used as a variable."""
        nxt = self.peek()
        return nxt.kind == "op" and nxt.value in ("=", ".", ":", "(", "[", ",", "+=", "-=", "..=")

    def for_stat(self) -> list[Piece]:
        line = self.expect("for").line
        first = self.name()
        if self.accept(":"):
            self.skip_type()
        if self.check("="):
            self.i += 1
            out = [Piece(line, "for"), Piece(first.line, first.value), Piece(line, "=")]
            out += self.expr()
            out.append(Piece(self.expect(",").line, ","))
            out += self.expr()
            if self.check(","):
                out.append(Piece(self.expect(",").line, ","))
                out += self.expr()
        else:
            out = [Piece(line, "for"), Piece(first.line, first.value)]
            while self.accept(","):
                n = self.name()
                if self.accept(":"):
                    self.skip_type()
                out += [Piece(n.line, ","), Piece(n.line, n.value)]
            in_line = self.expect("in").line
            exprs = self.expr()
            single = not self.check(",")
            while self.check(","):
                exprs.append(Piece(self.expect(",").line, ","))
                exprs += self.expr()
            out.append(Piece(in_line, "in"))
            # Only a single top-level expression can be a generalized-iteration target
            # (`for k, v in t`); commas inside a table literal or call do not count.
            if single and not _is_iterator_call(exprs):
                out += [Piece(in_line, "__iter(")] + exprs + [Piece(in_line, ")")]
            else:
                out += exprs
        out.append(Piece(self.expect("do").line, "do"))
        return out + self.loop_body() + [Piece(self.expect("end").line, "end")]

    def funcbody(self) -> list[Piece]:
        self.skip_generics()
        out = [Piece(self.expect("(").line, "(")]
        first = True
        while not self.check(")"):
            if not first:
                out.append(Piece(self.expect(",").line, ","))
            first = False
            if self.check("..."):
                t = self.expect("...")
                out.append(Piece(t.line, "..."))
                if self.accept(":"):
                    self.skip_type()
                continue
            n = self.name()
            out.append(Piece(n.line, n.value))
            if self.accept(":"):
                self.skip_type()
        out.append(Piece(self.expect(")").line, ")"))
        if self.accept(":"):
            self.skip_type()
        saved, self.loop_labels = self.loop_labels, []  # `continue` cannot cross a function
        body = self.block()
        self.loop_labels = saved
        return out + body + [Piece(self.expect("end").line, "end")]

    def expr_statement(self) -> list[Piece]:
        start = self.tok
        target = self.suffixedexp()
        if self.tok.kind == "op" and self.tok.value in COMPOUND:
            op = self.tok
            self.i += 1
            rhs = self.expr()
            if COMPOUND[op.value] == "..":
                return target + [Piece(op.line, "= __cat(")] + target + [Piece(op.line, ",")] + rhs + [Piece(op.line, ")")]
            return target + [Piece(op.line, "=")] + target + [Piece(op.line, COMPOUND[op.value] + " (")] + rhs + [Piece(op.line, ")")]
        if self.check("=") or self.check(","):
            out = list(target)
            while self.accept(","):
                out.append(Piece(self.tok.line, ","))
                out += self.suffixedexp()
            out.append(Piece(self.expect("=").line, "="))
            return out + self.exprlist()
        if not target or not self._ends_with_call:
            raise TranslateError(f"syntax error near {self.tok.value!r} (statement is not a call)", start.line)
        return target

    # -- expressions ---------------------------------------------------
    def exprlist(self) -> list[Piece]:
        out = self.expr()
        while self.check(","):
            out.append(Piece(self.expect(",").line, ","))
            out += self.expr()
        return out

    def expr(self, limit: int = 0) -> list[Piece]:
        t = self.tok
        if (t.kind == "keyword" and t.value == "not") or (t.kind == "op" and t.value in ("-", "#")):
            self.i += 1
            operand = self.expr(UNARY_PRIORITY)
            left = [Piece(t.line, "(" + t.value + (" " if t.value == "not" else ""))] + operand + [Piece(t.line, ")")]
        else:
            left = self.simpleexp()
        while True:
            op = self.tok
            if op.kind not in ("op", "keyword") or op.value not in BINARY_PRIORITY:
                return left
            lp, rp = BINARY_PRIORITY[op.value]
            if lp <= limit:
                return left
            self.i += 1
            right = self.expr(rp)
            if op.value == "..":
                left = [Piece(op.line, "__cat(")] + left + [Piece(op.line, ",")] + right + [Piece(op.line, ")")]
            else:
                left = [Piece(op.line, "(")] + left + [Piece(op.line, op.value)] + right + [Piece(op.line, ")")]

    def simpleexp(self) -> list[Piece]:
        t = self.tok
        if t.kind == "number":
            self.i += 1
            out = [Piece(t.line, _number(t.value))]
        elif t.kind == "string":
            if t.value.startswith("`"):
                raise TranslateError("interpolated strings are not supported by the test translator", t.line)
            self.i += 1
            out = [Piece(t.line, t.value)]
        elif t.kind == "keyword" and t.value in ("nil", "true", "false"):
            self.i += 1
            out = [Piece(t.line, t.value)]
        elif t.kind == "op" and t.value == "...":
            self.i += 1
            out = [Piece(t.line, "...")]
        elif t.kind == "keyword" and t.value == "function":
            self.i += 1
            out = [Piece(t.line, "function")] + self.funcbody()
        elif t.kind == "keyword" and t.value == "if":
            out = self.if_expression()
        elif t.kind == "op" and t.value == "{":
            out = self.table()
        else:
            out = self.suffixedexp()
        while self.check("::"):
            self.i += 1
            self.skip_type()
            out = [Piece(t.line, "(")] + out + [Piece(t.line, ")")]  # a cast also truncates to one value
        return out

    def if_expression(self) -> list[Piece]:
        line = self.expect("if").line
        out = [Piece(line, "((function() if")] + self.expr()
        out += [Piece(self.expect("then").line, "then return")] + self.expr()
        while self.check("elseif"):
            e = self.expect("elseif")
            out += [Piece(e.line, "elseif")] + self.expr() + [Piece(self.expect("then").line, "then return")] + self.expr()
        e = self.expect("else")
        out += [Piece(e.line, "else return")] + self.expr() + [Piece(e.line, "end end)())")]
        return out

    def table(self) -> list[Piece]:
        out = [Piece(self.expect("{").line, "{")]
        while not self.check("}"):
            t = self.tok
            if t.kind == "op" and t.value == "[":
                self.i += 1
                out.append(Piece(t.line, "["))
                out += self.expr()
                out.append(Piece(self.expect("]").line, "]"))
                out.append(Piece(self.expect("=").line, "="))
                out += self.expr()
            elif t.kind == "name" and self.peek().kind == "op" and self.peek().value == "=":
                self.i += 2
                out += [Piece(t.line, t.value), Piece(t.line, "=")] + self.expr()
            else:
                out += self.expr()
            if self.check(",") or self.check(";"):
                out.append(Piece(self.tok.line, self.tok.value))
                self.i += 1
            else:
                break
        out.append(Piece(self.expect("}").line, "}"))
        return out

    def suffixedexp(self) -> list[Piece]:
        t = self.tok
        self._ends_with_call = False
        if t.kind == "name":
            self.i += 1
            out = [Piece(t.line, t.value)]
        elif t.kind == "op" and t.value == "(":
            self.i += 1
            inner = self.expr()
            out = [Piece(t.line, "(")] + inner + [Piece(self.expect(")").line, ")")]
        else:
            raise TranslateError(f"unexpected symbol near {t.value!r}", t.line)
        while True:
            s = self.tok
            if s.kind == "op" and s.value == ".":
                self.i += 1
                n = self.name()
                out.append(Piece(s.line, "." + n.value))
                self._ends_with_call = False
            elif s.kind == "op" and s.value == "[":
                self.i += 1
                out.append(Piece(s.line, "["))
                out += self.expr()
                out.append(Piece(self.expect("]").line, "]"))
                self._ends_with_call = False
            elif s.kind == "op" and s.value == ":":
                self.i += 1
                n = self.name()
                out.append(Piece(s.line, ":" + n.value))
                out += self.call_args()
                self._ends_with_call = True
            elif (s.kind == "op" and s.value in ("(", "{")) or s.kind == "string":
                if s.kind == "op" and s.value == "(" and s.line != self.toks[self.i - 1].line:
                    # Lua 5.4 accepts this, Luau warns; keep it, it is a call either way.
                    pass
                out += self.call_args()
                self._ends_with_call = True
            else:
                return out

    def call_args(self) -> list[Piece]:
        t = self.tok
        if t.kind == "string":
            if t.value.startswith("`"):
                raise TranslateError("interpolated strings are not supported by the test translator", t.line)
            self.i += 1
            return [Piece(t.line, "("), Piece(t.line, t.value), Piece(t.line, ")")]
        if t.kind == "op" and t.value == "{":
            return [Piece(t.line, "(")] + self.table() + [Piece(t.line, ")")]
        out = [Piece(self.expect("(").line, "(")]
        if not self.check(")"):
            out += self.exprlist()
        out.append(Piece(self.expect(")").line, ")"))
        return out


def _is_iterator_call(exprs: list[Piece]) -> bool:
    """`pairs(x)`, `ipairs(x)`, `next, t` style iterators are left alone."""
    head = exprs[0].text if exprs else ""
    return head in ("pairs", "ipairs", "next", "string.gmatch", "utf8.codes") or head.endswith(":gmatch")


_WORDY = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _join(pieces: list[Piece]) -> str:
    out: list[str] = []
    line = 1
    prev = ""
    for p in pieces:
        if p.line > line:
            out.append("\n" * (p.line - line))
            line = p.line
        elif prev and (prev[-1] in _WORDY or prev[-1] in "-]") and (p.text[:1] in _WORDY or p.text[:1] in "-["):
            out.append(" ")
        elif prev:
            out.append(" ")
        text = p.text
        out.append(text)
        line += text.count("\n")
        prev = text
    return "".join(out) + "\n"


def translate(src: str) -> str:
    """Translate a Luau chunk to Lua 5.4 source with the same line numbers."""
    p = _Parser(src)
    p._ends_with_call = False
    pieces = p.block()
    if p.tok.kind != "eof":
        raise TranslateError(f"'{p.tok.value}' does not close a block here", p.tok.line)
    return _join(pieces)
