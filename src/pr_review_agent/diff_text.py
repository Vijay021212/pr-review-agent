"""How a file's change is shown to the model, and how much of the context it takes.

Each line carries its line number in the new file, so the model can point to exact lines.
Removed lines have no new number, so their number column is blank. This is the format
the ADR-001 model test used:

    ### src/stats.py (modified)
         1   def average(numbers):
         2 +     if not numbers:
           -     return total / count
"""

from __future__ import annotations

import math

from pr_review_agent.domain import ChangedFile, FileStatus, LineKind

CHARS_PER_TOKEN = 3
"""Code is usually about 4 characters per token; counting 3 overestimates on purpose, so a
prompt never silently runs past the model's context window (design section 6)."""

_MARKERS = {LineKind.ADDED: "+", LineKind.REMOVED: "-", LineKind.CONTEXT: " "}


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def render_file(file: ChangedFile) -> str:
    """The numbered diff of one file, with a header saying what happened to it."""
    rows = [f"### {file.path} ({_describe(file)})"]
    for line in file.lines:
        number = "" if line.new_number is None else str(line.new_number)
        rows.append(f"{number:>6} {_MARKERS[line.kind]} {line.text}")
    if not file.lines:
        rows.append("       (no line changes)")
    return "\n".join(rows)


def _describe(file: ChangedFile) -> str:
    if file.status is FileStatus.RENAMED:
        return f"renamed from {file.previous_path}"
    return {
        FileStatus.ADDED: "new file",
        FileStatus.REMOVED: "deleted",
        FileStatus.MODIFIED: "modified",
    }[file.status]
