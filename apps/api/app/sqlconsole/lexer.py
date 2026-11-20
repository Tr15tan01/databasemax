"""Minimal PostgreSQL lexer for the SQL editor.

It finds statement boundaries and the words of each statement while skipping comments, string
literals, quoted identifiers and dollar-quoted bodies. It is used for two things only: asking for
confirmation before obviously destructive statements, and making sure EXPLAIN wraps exactly one
statement. It never decides what is *allowed* to run — the database's own permissions do that,
and the SQL text is always sent to the server unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def _ident_char(c: str) -> bool:
    return c.isalnum() or c in "_$"


@dataclass
class Statement:
    text: str
    words: list[str] = field(default_factory=list)  # upper-cased keywords/identifiers, outside literals

    @property
    def keyword(self) -> str:
        return self.words[0] if self.words else ""


def split(sql: str) -> list[Statement]:
    """Statements in `sql` (empty ones dropped). Unterminated literals or comments run to the end
    of the text, as the server would treat them (it then reports the syntax error)."""
    out: list[Statement] = []
    n = len(sql)
    i = 0
    start = 0
    words: list[str] = []
    depth = 0  # parenthesis depth; a `;` inside parentheses doesn't end a statement

    def flush(end: int) -> None:
        if words:  # statements made only of comments/whitespace are dropped
            out.append(Statement(text=sql[start:end].strip(), words=list(words)))

    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if c == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
        elif c == "/" and nxt == "*":
            level, i = 1, i + 2
            while i < n and level:
                if sql.startswith("/*", i):
                    level, i = level + 1, i + 2
                elif sql.startswith("*/", i):
                    level, i = level - 1, i + 2
                else:
                    i += 1
        elif c == "'":
            escapes = i > 0 and sql[i - 1] in "eE" and (i < 2 or not _ident_char(sql[i - 2]))
            i += 1
            while i < n:
                if escapes and sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        elif c == '"':
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        elif c == "$" and (i == 0 or not _ident_char(sql[i - 1])) and (m := _DOLLAR_TAG.match(sql, i)):
            tag = m.group(0)
            end = sql.find(tag, m.end())
            i = n if end < 0 else end + len(tag)
        elif c == "(":
            depth += 1
            i += 1
        elif c == ")":
            depth = max(depth - 1, 0)
            i += 1
        elif c == ";" and depth == 0:
            flush(i)
            words = []
            i += 1
            start = i
        elif (c.isalpha() or c == "_") and (i == 0 or not _ident_char(sql[i - 1])):
            m = _WORD.match(sql, i)
            assert m is not None
            words.append(m.group(0).upper())
            i = m.end()
        else:
            i += 1
    flush(n)
    return out


# Statements that remove or rewrite existing data or schema. Only used to ask "are you sure?".
_DESTRUCTIVE = {"DROP", "TRUNCATE", "DELETE", "UPDATE", "ALTER"}


def destructive_kinds(statements: list[Statement]) -> list[str]:
    """Human-readable labels for statements that should be confirmed, in order, de-duplicated."""
    found: list[str] = []
    for st in statements:
        kw = st.keyword
        labels: list[str] = []
        if kw in ("DELETE", "UPDATE"):
            labels.append(kw if "WHERE" in st.words else f"{kw} without WHERE")
        elif kw in _DESTRUCTIVE:
            labels.append(kw)
        elif kw == "WITH":
            labels.extend(w for w in ("DELETE", "UPDATE") if w in st.words)
        for label in labels:
            if label not in found:
                found.append(label)
    return found


def single_statement(sql: str) -> str | None:
    """The only statement in `sql` without its trailing semicolon, or None if there isn't
    exactly one."""
    statements = split(sql)
    if len(statements) != 1:
        return None
    return statements[0].text.rstrip().rstrip(";").rstrip()
