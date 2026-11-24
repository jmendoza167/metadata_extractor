"""Every .py file in src/, tests/ and scripts/ must be valid Python 3.10, the
oldest version metaex supports (requires-python in pyproject.toml).

ast.parse(..., feature_version=(3, 10)) rejects most newer syntax (except*,
type statements, type parameters), but on Python 3.12 and later it accepts
the f-strings of PEP 701 that 3.10 and 3.11 refuse: a backslash or a comment
inside a replacement field, a quote reused inside one, a line break inside a
single-quoted one. Those are found here from the tokens, which on 3.12+ show
each part of an f-string. On 3.10 and 3.11, ast.parse itself refuses them.
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OLDEST = (3, 10)
PY_FILES = sorted(p for folder in ("src", "tests", "scripts")
                  for p in (ROOT / folder).rglob("*.py"))
FSTRING_START = getattr(tokenize, "FSTRING_START", None)  # Python 3.12+
FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)
FSTRING_END = getattr(tokenize, "FSTRING_END", None)


def quote_of(token_text: str) -> str:
    """The opening quote of a string token: ', ", ''' or \"\"\"."""
    body = token_text.lstrip("rRbBuUfFtT")
    return body[:3] if body[:3] in ("'''", '"""') else body[:1]


def clashes(inner: str, outer: str) -> bool:
    """Before 3.12, a string inside an f-string ended it when it used the
    same quote (for triple quotes: the same triple quote)."""
    return inner == outer if len(outer) == 3 else inner[0] == outer


def fstring_problems(source: str) -> list[str]:
    """PEP 701 f-string syntax (Python 3.12+), found from the tokens."""
    if FSTRING_START is None:
        return []  # before 3.12, ast.parse refuses it (see problems)
    problems: list[str] = []
    outer: list[str] = []  # quotes of the f-strings we are inside, innermost last
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        where = f"line {tok.start[0]}"
        if tok.type == FSTRING_END:
            outer.pop()
            continue
        if outer and tok.type != FSTRING_MIDDLE:  # inside a replacement field
            if tok.type in (tokenize.STRING, FSTRING_START):
                quote = quote_of(tok.string)
                if any(clashes(quote, q) for q in outer):
                    problems.append(f"{where}: quote {quote} reused inside an f-string")
            if tok.type == tokenize.STRING and "\\" in tok.string:
                problems.append(f"{where}: backslash inside an f-string expression")
            if tok.type == tokenize.COMMENT:
                problems.append(f"{where}: comment inside an f-string expression")
            if tok.type in (tokenize.NL, tokenize.NEWLINE) and len(outer[-1]) == 1:
                problems.append(f"{where}: line break inside a single-quoted f-string")
        if tok.type == FSTRING_START:
            outer.append(quote_of(tok.string))
    return problems


def starred_problems(tree: ast.AST) -> list[str]:
    problems = []
    for node in ast.walk(tree):
        # Python 3.11+ (PEP 646), not refused by feature_version.
        if isinstance(node, ast.Subscript):
            items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if any(isinstance(item, ast.Starred) for item in items):
                problems.append(f"line {node.lineno}: starred expression in a subscript")
        if isinstance(node, ast.arg) and isinstance(node.annotation, ast.Starred):
            problems.append(f"line {node.lineno}: starred annotation")
    return problems


def problems(source: str) -> list[str]:
    try:
        tree = ast.parse(source, feature_version=OLDEST)
    except SyntaxError as e:
        return [f"line {e.lineno}: {e.msg}"]
    return starred_problems(tree) + fstring_problems(source)


def test_every_python_file_is_valid_python_3_10():
    assert PY_FILES, "no .py files found"
    found = [f"{path.relative_to(ROOT).as_posix()}: {problem}"
             for path in PY_FILES for problem in problems(path.read_text(encoding="utf-8"))]
    assert found == []


# Sources that need Python 3.11 or later (only checked where this Python can
# parse them at all).
NEWER = {
    "backslash in an f-string expression": 'x = f\'{g(p="<a \\"b\\"/>")}\'\n',
    "same quote reused": 'x = f"{d["k"]}"\n',
    "same quote in a nested f-string": "x = f'{f'{1}'}'\n",
    "same triple quote reused": 'x = f"""{"""a"""}"""\n',
    "comment in an f-string expression": 'x = f"""{a  # note\n}"""\n',
    "line break in a single-quoted f-string": 'x = f"{a +\n b}"\n',
    "except*": "try:\n    pass\nexcept* ValueError:\n    pass\n",
    "type statement": "type X = int\n",
    "type parameters": "def f[T](x: T) -> T:\n    return x\n",
    "starred subscript": "x = a[*b]\n",
    "starred annotation": "def f(*args: *Ts):\n    pass\n",
}

VALID_3_10 = {
    "plain f-string": 'x = f"{a} and {b!r:>10}"\n',
    "other quote inside": "x = f\"{d['k']}\" + f'{d[\"k\"]}'\n",
    "single quote inside a triple-quoted f-string": "x = f'''{d['k']}'''\n",
    "line break in a triple-quoted f-string": 'x = f"""{a +\n b}"""\n',
    "backslash outside the expression": 'x = f"\\n{a}\\t"\n',
    "nested f-string with other quotes": "x = f\"{f'{a}'}\"\n",
    "match": "match x:\n    case 1:\n        pass\n",
    "parenthesized with": "with (open(a) as b, open(c) as d):\n    pass\n",
    "subscript with a slice": "x = a[1:2, ::3]\n",
}


def parses_here(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


@pytest.mark.parametrize("source", list(NEWER.values()), ids=list(NEWER))
def test_newer_syntax_is_found(source):
    if not parses_here(source):
        pytest.skip(f"Python {sys.version_info[0]}.{sys.version_info[1]} can't parse it")
    assert problems(source)


@pytest.mark.parametrize("source", list(VALID_3_10.values()), ids=list(VALID_3_10))
def test_python_3_10_syntax_is_accepted(source):
    assert problems(source) == []
