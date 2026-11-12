"""The prompts, kept in text files next to this module rather than inside the code.

Each file starts with a version line, `version: 3`, followed by the prompt itself. The
version goes into the summary and the run log (FR-23), so a change in evaluation scores
can be traced back to a change in a prompt. Change the version whenever the text changes.

Pull request text and code go into the user message inside `data_block()` markers. Every
prompt tells the model that what's inside them is data to review, never instructions
(design section 7, NFR-5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

_VERSION_LINE = re.compile(r"version: (\d+)")


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
    """The prompt in `<name>.md`."""
    raw = (
        resources.files("pr_review_agent.prompts")
        .joinpath(f"{name}.md")
        .read_text(encoding="utf-8")
    )
    return parse(name, raw)


def parse(name: str, raw: str) -> Prompt:
    first_line, _, text = raw.partition("\n")
    version = _VERSION_LINE.fullmatch(first_line.strip())
    if version is None:
        raise ValueError(f"the prompt {name} must start with a line like 'version: 1'")
    return Prompt(name=name, version=int(version[1]), text=text.strip())


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
