# compiler/lexer.py
from enum import Enum, auto
from dataclasses import dataclass
from typing import List, Optional
from compiler.errors import KolError, SourceLocation

class TokenType(Enum):
    # Keywords
    LET = auto()
    MUT = auto()
    CONST = auto()
    FN = auto()
    RETURN = auto()
    PURE = auto()
    TASK = auto()
    AWAIT = auto()
    TYPE = auto()
    ENUM = auto()
    IMPL = auto()
    INTERFACE = auto()
    SELF = auto()
    SELF_TYPE = auto()
    IF = auto()
    ELIF = auto()
    ELSE = auto()
    MATCH = auto()
    END = auto()
    FOR = auto()
    WHILE = auto()
    LOOP = auto()
    BREAK = auto()
    CONTINUE = auto()
    IN = auto()
    STEP = auto()
    FAIL = auto()
    TRY = auto()
    OR = auto()
    CATCH = auto()
    ARENA = auto()
    DEFER = auto()
    CHANNEL = auto()
    ALL = auto()
    USE = auto()
    FROM = auto()
    EXTERN = auto()
    WHEN = auto()
    WHERE = auto()
    ANY = auto()
    SYSTEM = auto()
    RAW = auto()
    ASM = auto()
    TRUE = auto()
    FALSE = auto()
    NONE = auto()
    NOT = auto()
    AND = auto()
    TEST = auto()
    MOD = auto()
    PERCENT = auto()
    ASSERT = auto()

    # Literals & Identifiers
    INT_LIT = auto()
    FLOAT_LIT = auto()
    STR_LIT = auto()
    IDENT = auto()

    # String Interpolation
    STR_INTERP_START = auto()
    STR_INTERP_MID = auto()
    STR_INTERP_END = auto()

    # Operators
    PLUS = auto()           # +
    MINUS = auto()          # -
    STAR = auto()           # *
    SLASH = auto()          # /
    POWER = auto()          # **
    EQ = auto()             # ==
    NEQ = auto()            # !=
    LT = auto()             # <
    GT = auto()             # >
    LTE = auto()            # <=
    GTE = auto()            # >=
    AMP_AMP = auto()        # &&
    PIPE_PIPE = auto()      # ||
    BANG = auto()           # !
    AMP = auto()            # &
    PIPE = auto()           # |
    CARET = auto()          # ^
    TILDE = auto()          # ~
    LSHIFT = auto()         # <<
    RSHIFT = auto()         # >>
    RANGE = auto()          # ..
    RANGE_INC = auto()      # ..=
    PIPE_OP = auto()        # |>
    QUESTION = auto()       # ?
    ARROW = auto()          # ->
    ASSIGN = auto()         # =
    PLUS_ASSIGN = auto()    # +=
    MINUS_ASSIGN = auto()   # -=
    STAR_ASSIGN = auto()    # *=
    SLASH_ASSIGN = auto()   # /=

    # Delimiters
    LPAREN = auto()         # (
    RPAREN = auto()         # )
    LBRACKET = auto()       # [
    RBRACKET = auto()       # ]
    LBRACE = auto()         # {
    RBRACE = auto()         # }
    COLON = auto()          # :
    COMMA = auto()          # ,
    DOT = auto()            # .
    NEWLINE = auto()
    EOF = auto()

KEYWORDS = {
    "assert": TokenType.ASSERT,
    "let": TokenType.LET,
    "mut": TokenType.MUT,
    "const": TokenType.CONST,
    "fn": TokenType.FN,
    "return": TokenType.RETURN,
    "pure": TokenType.PURE,
    "task": TokenType.TASK,
    "await": TokenType.AWAIT,
    "type": TokenType.TYPE,
    "enum": TokenType.ENUM,
    "impl": TokenType.IMPL,
    "interface": TokenType.INTERFACE,
    "self": TokenType.SELF,
    "Self": TokenType.SELF_TYPE,
    "if": TokenType.IF,
    "elif": TokenType.ELIF,
    "else": TokenType.ELSE,
    "match": TokenType.MATCH,
    "end": TokenType.END,
    "for": TokenType.FOR,
    "while": TokenType.WHILE,
    "loop": TokenType.LOOP,
    "break": TokenType.BREAK,
    "continue": TokenType.CONTINUE,
    "in": TokenType.IN,
    "step": TokenType.STEP,
    "fail": TokenType.FAIL,
    "try": TokenType.TRY,
    "or": TokenType.OR,
    "catch": TokenType.CATCH,
    "arena": TokenType.ARENA,
    "defer": TokenType.DEFER,
    "channel": TokenType.CHANNEL,
    "all": TokenType.ALL,
    "use": TokenType.USE,
    "from": TokenType.FROM,
    "extern": TokenType.EXTERN,
    "when": TokenType.WHEN,
    "where": TokenType.WHERE,
    "any": TokenType.ANY,
    "system": TokenType.SYSTEM,
    "raw": TokenType.RAW,
    "asm": TokenType.ASM,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "none": TokenType.NONE,
    "not": TokenType.NOT,
    "and": TokenType.AND,
    "test": TokenType.TEST,
    "mod": TokenType.MOD,
}

@dataclass
class Token:
    type: TokenType
    value: str
    location: SourceLocation

class Lexer:
    def __init__(self, filename: str, source: str):
        self.filename = filename
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: List[Token] = []
        self.string_mode_stack: List[int] = []

    def _curr(self) -> str:
        if self.pos < len(self.source):
            return self.source[self.pos]
        return ""

    def _peek(self, offset: int = 1) -> str:
        idx = self.pos + offset
        if idx < len(self.source):
            return self.source[idx]
        return ""

    def _advance(self) -> str:
        ch = self._curr()
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def tokenize(self) -> List[Token]:
        nesting_depth = 0

        while self.pos < len(self.source):
            ch = self._curr()

            # Skip comments: --
            if ch == '-' and self._peek() == '-':
                while self._curr() and self._curr() != '\n':
                    self._advance()
                continue

            if ch == '\n':
                loc = SourceLocation(self.filename, self.line, self.col)
                self._advance()
                if nesting_depth == 0:
                    if not self.tokens or self.tokens[-1].type != TokenType.NEWLINE:
                        self.tokens.append(Token(TokenType.NEWLINE, "\n", loc))
                continue

            if ch.isspace():
                self._advance()
                continue

            loc = SourceLocation(self.filename, self.line, self.col)

            # Check if closing brace for string interpolation
            if ch == '}' and self.string_mode_stack and nesting_depth == self.string_mode_stack[-1]:
                self.string_mode_stack.pop()
                nesting_depth -= 1
                self._advance()
                nesting_depth = self._scan_string_continuation(loc, nesting_depth)
                continue

            if ch == '(':
                nesting_depth += 1
                self._advance()
                self.tokens.append(Token(TokenType.LPAREN, "(", loc))
                continue
            elif ch == ')':
                if nesting_depth > 0: nesting_depth -= 1
                self._advance()
                self.tokens.append(Token(TokenType.RPAREN, ")", loc))
                continue
            elif ch == '[':
                nesting_depth += 1
                self._advance()
                self.tokens.append(Token(TokenType.LBRACKET, "[", loc))
                continue
            elif ch == ']':
                if nesting_depth > 0: nesting_depth -= 1
                self._advance()
                self.tokens.append(Token(TokenType.RBRACKET, "]", loc))
                continue
            elif ch == '{':
                nesting_depth += 1
                self._advance()
                self.tokens.append(Token(TokenType.LBRACE, "{", loc))
                continue
            elif ch == '}':
                if nesting_depth > 0: nesting_depth -= 1
                self._advance()
                self.tokens.append(Token(TokenType.RBRACE, "}", loc))
                continue

            # String literal / String interpolation
            if ch == '"':
                nesting_depth = self._scan_string(loc, nesting_depth)
                continue

            # Numbers
            if ch.isdigit():
                self._scan_number(loc)
                continue

            # Identifiers and Keywords
            if ch.isalpha() or ch == '_':
                self._scan_ident(loc)
                continue

            # Multi-character operators
            if ch == '.' and self._peek() == '.' and self._peek(2) == '=':
                self._advance(); self._advance(); self._advance()
                self.tokens.append(Token(TokenType.RANGE_INC, "..=", loc))
                continue
            elif ch == '.' and self._peek() == '.':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.RANGE, "..", loc))
                continue
            elif ch == '|' and self._peek() == '>':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.PIPE_OP, "|>", loc))
                continue
            elif ch == '-' and self._peek() == '>':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.ARROW, "->", loc))
                continue
            elif ch == '=' and self._peek() == '=':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.EQ, "==", loc))
                continue
            elif ch == '!' and self._peek() == '=':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.NEQ, "!=", loc))
                continue
            elif ch == '<' and self._peek() == '=':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.LTE, "<=", loc))
                continue
            elif ch == '>' and self._peek() == '=':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.GTE, ">=", loc))
                continue
            elif ch == '<' and self._peek() == '<':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.LSHIFT, "<<", loc))
                continue
            elif ch == '>' and self._peek() == '>':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.RSHIFT, ">>", loc))
                continue
            elif ch == '&' and self._peek() == '&':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.AMP_AMP, "&&", loc))
                continue
            elif ch == '|' and self._peek() == '|':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.PIPE_PIPE, "||", loc))
                continue
            elif ch == '*' and self._peek() == '*':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.POWER, "**", loc))
                continue
            elif ch == '+' and self._peek() == '=':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.PLUS_ASSIGN, "+=", loc))
                continue
            elif ch == '-' and self._peek() == '=':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.MINUS_ASSIGN, "-=", loc))
                continue
            elif ch == '*' and self._peek() == '=':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.STAR_ASSIGN, "*=", loc))
                continue
            elif ch == '/' and self._peek() == '=':
                self._advance(); self._advance()
                self.tokens.append(Token(TokenType.SLASH_ASSIGN, "/=", loc))
                continue

            op_map = {
                '+': TokenType.PLUS, '-': TokenType.MINUS, '*': TokenType.STAR, '/': TokenType.SLASH,
                '%': TokenType.PERCENT,
                '<': TokenType.LT, '>': TokenType.GT, '=': TokenType.ASSIGN, '!': TokenType.BANG,
                '&': TokenType.AMP, '|': TokenType.PIPE, '^': TokenType.CARET, '~': TokenType.TILDE,
                '?': TokenType.QUESTION, ':': TokenType.COLON, ',': TokenType.COMMA, '.': TokenType.DOT,
            }

            if ch in op_map:
                self.tokens.append(Token(op_map[ch], ch, loc))
                self._advance()
                continue

            raise KolError(f"Unexpected character: '{ch}'", loc)

        loc = SourceLocation(self.filename, self.line, self.col)
        self.tokens.append(Token(TokenType.EOF, "", loc))
        return self.tokens

    def _scan_number(self, loc: SourceLocation):
        start = self.pos
        is_float = False
        while self._curr().isdigit():
            self._advance()
        if self._curr() == '.' and self._peek().isdigit():
            is_float = True
            self._advance()
            while self._curr().isdigit():
                self._advance()
        val = self.source[start:self.pos]
        if is_float:
            self.tokens.append(Token(TokenType.FLOAT_LIT, val, loc))
        else:
            self.tokens.append(Token(TokenType.INT_LIT, val, loc))

    def _scan_ident(self, loc: SourceLocation):
        start = self.pos
        while self._curr().isalnum() or self._curr() == '_':
            self._advance()
        val = self.source[start:self.pos]
        if val in KEYWORDS:
            self.tokens.append(Token(KEYWORDS[val], val, loc))
        else:
            self.tokens.append(Token(TokenType.IDENT, val, loc))

    def _scan_string(self, loc: SourceLocation, nesting_depth: int) -> int:
        self._advance() # consume initial '"'
        buf = []
        has_interp = False

        while self._curr() and self._curr() != '"':
            if self._curr() == '\\':
                self._advance()
                esc = self._advance()
                if esc == 'n': buf.append('\n')
                elif esc == 't': buf.append('\t')
                elif esc == 'r': buf.append('\r')
                elif esc == '"': buf.append('"')
                elif esc == '\\': buf.append('\\')
                elif esc == '{': buf.append('{')
                elif esc == '}': buf.append('}')
                else: buf.append(esc)
            elif self._curr() == '{':
                has_interp = True
                break
            else:
                buf.append(self._advance())

        if has_interp:
            prefix = "".join(buf)
            self.tokens.append(Token(TokenType.STR_INTERP_START, prefix, loc))
            self._advance() # consume '{'
            new_depth = nesting_depth + 1
            self.string_mode_stack.append(new_depth)
            return new_depth
        else:
            if self._curr() != '"':
                raise KolError("Unterminated string literal", loc)
            self._advance() # consume final '"'
            self.tokens.append(Token(TokenType.STR_LIT, "".join(buf), loc))
            return nesting_depth

    def _scan_string_continuation(self, loc: SourceLocation, nesting_depth: int) -> int:
        buf = []
        has_interp = False
        while self._curr() and self._curr() != '"':
            if self._curr() == '\\':
                self._advance()
                esc = self._advance()
                if esc == 'n': buf.append('\n')
                elif esc == 't': buf.append('\t')
                elif esc == 'r': buf.append('\r')
                elif esc == '"': buf.append('"')
                elif esc == '\\': buf.append('\\')
                elif esc == '{': buf.append('{')
                elif esc == '}': buf.append('}')
                else: buf.append(esc)
            elif self._curr() == '{':
                has_interp = True
                break
            else:
                buf.append(self._advance())

        text = "".join(buf)
        if has_interp:
            self.tokens.append(Token(TokenType.STR_INTERP_MID, text, loc))
            self._advance() # consume '{'
            new_depth = nesting_depth + 1
            self.string_mode_stack.append(new_depth)
            return new_depth
        else:
            if self._curr() != '"':
                raise KolError("Unterminated string literal", loc)
            self._advance() # consume final '"'
            self.tokens.append(Token(TokenType.STR_INTERP_END, text, loc))
            return nesting_depth
