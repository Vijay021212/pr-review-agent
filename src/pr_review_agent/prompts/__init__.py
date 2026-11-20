"""The prompts, kept in text files next to this module rather than inside the code.

Each file starts with a version line, `version: 3`, followed by the prompt itself. The
version goes into the summary and the run log (FR-23), so a change in evaluation scores
can be traced back to a change in a prompt. Change the version whenever the text changes.

Text shared by several prompts, like the severity definitions, lives once in `parts/` and
is included with `{{part:severity}}`. Changing a part changes every prompt that includes
it, so bump those prompts' versions too.

Pull request text and code go into the user message inside `data_block()` markers. Every
prompt tells the model that what's inside them is data to review, never instructions
(design section 7, NFR-5). A small model can follow such text anyway, so the passages of a
pull request that address the reviewer, which the screen stage quotes, are also left out of
what the later stages read: of its own text with `leave_out()`, and of the code with
`leave_out_comments()`, which only ever touches a line that is only a comment.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib import resources

_VERSION_LINE = re.compile(r"version: (\d+)")
_PART = re.compile(r"\{\{part:([a-z_]+)\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def label(self) -> str:
        """How the prompt is named in the summary and the run log, for example "understand v1"."""
        return f"{self.name} v{self.version}"


@cache
def load(name: str) -> Prompt:
    """The prompt in `<name>.md`, with its parts filled in."""
    return parse(name, _read(f"{name}.md"))


def parse(name: str, raw: str, read_part: Callable[[str], str] | None = None) -> Prompt:
    first_line, _, text = raw.partition("\n")
    version = _VERSION_LINE.fullmatch(first_line.strip())
    if version is None:
        raise ValueError(f"the prompt {name} must start with a line like 'version: 1'")
    read = read_part or (lambda part: _read(f"parts/{part}.md"))
    text = _PART.sub(lambda match: read(match[1]).strip(), text)
    return Prompt(name=name, version=int(version[1]), text=text.strip())


_SEVERITY_LINE = re.compile(r"^- \w+: (?P<text>.+)$", re.MULTILINE)


@cache
def severity_definitions() -> tuple[str, ...]:
    """The sentences that define the severity levels, and their examples, as every prompt
    includes them (requirements 5.2): read from `parts/severity.md`, so code that looks for
    them can't drift from the prompts. Without "Example:" and the closing full stop."""
    return tuple(
        sentence.removeprefix("Example:").strip().rstrip(".")
        for line in _SEVERITY_LINE.finditer(_read("parts/severity.md"))
        for sentence in re.split(r"(?<=\.)\s+", line["text"].strip())
    )


def _read(path: str) -> str:
    folder = resources.files("pr_review_agent.prompts")
    for part in path.split("/"):
        folder = folder.joinpath(part)
    return folder.read_text(encoding="utf-8")


def data_block(label: str, text: str) -> str:
    """`text` between markers, so the model can tell data from instructions.

    Anything in `text` that looks like a marker is broken up, so a pull request can't end
    the block early and continue with text that poses as instructions.
    """
    safe = text.replace("<<<", "<< <").replace(">>>", "> >>")
    return f"<<<BEGIN {label}>>>\n{safe}\n<<<END {label}>>>"


def clip(text: str, limit: int) -> str:
    """`text`, cut to `limit` characters with a note saying how much was left out."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[... {len(text) - limit} more characters left out]"


LEFT_OUT = "[A passage addressed to the reviewer was left out here.]"


def appears_in(passage: str, text: str) -> bool:
    """Whether `passage` is in `text` word for word, whatever the spaces between its words."""
    pattern = _words_of(passage)
    return pattern is not None and pattern.search(text) is not None


def leave_out(text: str, passages: tuple[str, ...]) -> str:
    """The pull request's own text (its title, its description, or text the model wrote
    from them) with each passage replaced by a note that it was left out (NFR-5).

    The passages are the sentences that tell the reviewer what to do or conclude, as the
    screen stage quoted them. Each is found word for word, whatever the spaces and line
    breaks between its words; a quote that isn't in the text changes nothing, so nothing is
    ever removed on the model's word alone. Never use it on code: `leave_out_comments()`.
    """
    for passage in passages:
        pattern = _words_of(passage)
        if pattern is not None:
            text = pattern.sub(lambda _: LEFT_OUT, text)
    return text


# A line that is only a comment, in the common languages: `#`, `//`, `/* ... */`, `*`, `--`.
# After `#`, `*` and `--` a space must follow, as it does in comments, so that lines of code
# that start with them, like `*ptr = 1;`, `--count;`, `*rest, last = parts` or `#define`,
# are never taken for comments (#109).
COMMENT_LINE = re.compile(
    r"^\s*(?:(?:#+|\*+|--+)(?:\s+|$)|(?://+|/\*+)\s*)(?P<text>.*?)\s*(?:\*+/)?\s*$"
)
# How a line of code looks in what the model reads, before the code itself: a line of the
# numbered diff ("    12 + ", or six spaces for a removed line: "       - "), a numbered
# line of a file ("    12  ") or a search match ("src/a.py:12: ").
_VIEW_PREFIX = re.compile(r"(?: *\d+| {6}) [+\- ] | *\d+  |[^\s:][^:\n]*:\d+: ")


def leave_out_comments(code: str, passages: tuple[str, ...]) -> str:
    """`code`, as the model reads it, with each comment line whose whole text is one of the
    passages replaced by a note that it was left out (NFR-5, #82).

    Only a line that is only a comment can be left out, and only all of its text: its line
    number and comment marker stay, and a line of code never changes, even when a passage's
    words are in it, so a comment that repeats a line of code can't hide that line (#109).
    """
    wanted = {" ".join(passage.split()) for passage in passages}
    if not wanted:
        return code
    lines = code.split("\n")
    for index, line in enumerate(lines):
        prefix = _VIEW_PREFIX.match(line)
        start = prefix.end() if prefix else 0
        comment = COMMENT_LINE.match(line[start:])
        if comment and " ".join(comment["text"].split()) in wanted:
            first, last = comment.span("text")
            lines[index] = line[: start + first] + LEFT_OUT + line[start + last :]
    return "\n".join(lines)


def _words_of(passage: str) -> re.Pattern[str] | None:
    words = passage.split()
    if not words:
        return None
    return re.compile(r"\s+".join(re.escape(word) for word in words))
