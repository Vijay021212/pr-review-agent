"""The comments of a file, by its language, and hiding the ones the screen leaves out (NFR-5).

The screen stage leaves out a comment the change adds that tells the reviewer what to do
(#82). Whether a line is a comment depends on the language: `#` starts a comment in Python
and a directive in C, and `*` or `//` start a comment in C but are operators in Python. So
comments are recognised only in the languages listed here, by the file's extension; in any
other file none is, which can only leave a comment in, never take code for one.

The screen records each comment line it leaves out by its place, the file and the line, and
the diffs and the files the later stages read hide those lines and only those (#109): a
comment that repeats a line of code can't hide that line, whatever its text. The project map
doesn't hide them yet (#104).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from pr_review_agent.prompts import LEFT_OUT


@dataclass(frozen=True)
class Language:
    """How comments are written in one family of languages."""

    line: tuple[str, ...]  # what starts a comment that runs to the end of the line
    blocks: bool = False  # block comments in the C style, /* ... */
    code: tuple[str, ...] = ()  # code that starts like a comment, like a PHP attribute: #[


_HASH = Language(line=("#",))
_C = Language(line=("//",), blocks=True)
_DASHES = Language(line=("--",))

_BY_EXTENSION: dict[str, Language] = {
    **dict.fromkeys(
        ("py", "pyi", "sh", "bash", "zsh", "rb", "pl", "pm", "r", "ps1", "psm1"), _HASH
    ),
    **dict.fromkeys(("yaml", "yml", "toml", "cfg", "conf", "tf", "cmake", "mk"), _HASH),
    **dict.fromkeys(("c", "h", "cc", "cpp", "cxx", "hpp", "hh", "cs", "java", "go", "rs"), _C),
    **dict.fromkeys(("js", "jsx", "mjs", "cjs", "ts", "tsx", "swift", "kt", "kts", "scala"), _C),
    **dict.fromkeys(("dart", "groovy"), _C),
    "php": Language(line=("//", "#"), blocks=True, code=("#[",)),
    **dict.fromkeys(("css", "scss", "less"), Language(line=(), blocks=True)),
    **dict.fromkeys(("sql", "lua", "hs"), _DASHES),
}
_BY_NAME = {"Dockerfile": _HASH, "Makefile": _HASH}


def language_of(path: str) -> Language | None:
    """How comments are written in the file at `path`, or None when it isn't known."""
    name = PurePosixPath(path).name
    if name in _BY_NAME:
        return _BY_NAME[name]
    return _BY_EXTENSION.get(PurePosixPath(path).suffix.lower().removeprefix("."))


def comment_spans(path: str, lines: Sequence[str]) -> list[tuple[int, int] | None]:
    """For each of `lines`, consecutive lines of the file at `path`: where the text of the
    comment is, when the line is only a comment, or None.

    A block comment that started before the first line isn't known, so its middle lines
    count as code; that can only leave a comment in. A line with code before a comment, or
    after the end of a block comment, isn't only a comment.
    """
    language = language_of(path)
    spans: list[tuple[int, int] | None] = []
    in_block = False
    for line in lines:
        span: tuple[int, int] | None = None
        start = len(line) - len(line.lstrip())
        if language is None or any(line.startswith(code, start) for code in language.code):
            pass
        elif in_block:
            span, in_block = _in_block(line, start)
        elif marker := next((m for m in language.line if line.startswith(m, start)), None):
            span = _text(line, start + len(marker.rstrip()), len(line), marker[0])
        elif language.blocks and line.startswith("/*", start):
            span, in_block = _in_block(line, start + 2)
        spans.append(span)
    return spans


def _in_block(line: str, start: int) -> tuple[tuple[int, int] | None, bool]:
    """The text of a line of a block comment from `start`, and whether the block goes on."""
    end = line.find("*/", start)
    if end == -1:
        return _text(line, start, len(line), "*"), True
    if line[end + 2 :].strip():
        return None, False  # code after the comment: not only a comment
    return _text(line, start, end, "*"), False


def _text(line: str, start: int, end: int, repeated: str) -> tuple[int, int]:
    """The comment's text between `start` and `end`, after more of the marker (as in ### or
    a block's leading *) and spaces."""
    while start < end and line[start] in f"{repeated} \t":
        start += 1
    while end > start and line[end - 1] in " \t":
        end -= 1
    return start, end


def hidden_by_file(lines: Iterable[tuple[str, int]]) -> dict[str, frozenset[int]]:
    """The places of the hidden comments, (file, line) pairs, as the lines of each file."""
    by_file: dict[str, set[int]] = {}
    for path, number in lines:
        by_file.setdefault(path, set()).add(number)
    return {path: frozenset(numbers) for path, numbers in by_file.items()}


def hide(path: str, lines: Sequence[str], numbers: Collection[int], first: int = 1) -> list[str]:
    """`lines` of the file at `path`, the first being line `first`, with the comment of each
    line in `numbers` replaced by a note that it was left out; its indentation and marker
    stay. A line that isn't only a comment never changes."""
    if not numbers:
        return list(lines)
    spans = comment_spans(path, lines)
    hidden = list(lines)
    for index, span in enumerate(spans):
        if span is not None and first + index in numbers:
            start, end = span
            line = hidden[index]
            hidden[index] = line[:start] + LEFT_OUT + line[end:]
    return hidden
