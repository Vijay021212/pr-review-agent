"""Turn the patch text of one file into diff lines with their line numbers.

GitHub sends each file's change as unified-diff hunks:

    @@ -10,3 +10,4 @@ def total(items):
         count = 0              <- context: old line 10, new line 10
    -    return sum(items)      <- removed: old line 11
    +    if not items:          <- added:   new line 11
    +        return 0           <- added:   new line 12

The header says where each hunk starts and how many old and new lines it covers. Counting
with those numbers, rather than trusting line breaks, keeps the numbering right even when
the patch ends with an empty line.
"""

from __future__ import annotations

import re

from pr_review_agent.domain import DiffLine, LineKind

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_patch(patch: str) -> tuple[DiffLine, ...]:
    """Every line of every hunk in `patch`, numbered as in the old and the new file."""
    lines: list[DiffLine] = []
    old_number = new_number = old_left = new_left = 0
    for raw in patch.split("\n"):
        header = _HUNK_HEADER.match(raw)
        if header:
            old_number, new_number = int(header[1]), int(header[3])
            # A count of 1 is left out of the header: "@@ -5 +5 @@".
            old_left = int(header[2]) if header[2] is not None else 1
            new_left = int(header[4]) if header[4] is not None else 1
            continue
        if old_left <= 0 and new_left <= 0:
            continue  # outside a hunk, or "\ No newline at end of file" after its last line
        text = raw.removesuffix("\r")
        marker, content = text[:1], text[1:]
        if marker == "+":
            lines.append(DiffLine(kind=LineKind.ADDED, new_number=new_number, text=content))
            new_number, new_left = new_number + 1, new_left - 1
        elif marker == "-":
            lines.append(DiffLine(kind=LineKind.REMOVED, old_number=old_number, text=content))
            old_number, old_left = old_number + 1, old_left - 1
        elif marker == "\\":
            continue  # "\ No newline at end of file" inside a hunk
        else:
            lines.append(
                DiffLine(
                    kind=LineKind.CONTEXT,
                    old_number=old_number,
                    new_number=new_number,
                    text=content,
                )
            )
            old_number, new_number = old_number + 1, new_number + 1
            old_left, new_left = old_left - 1, new_left - 1
    return tuple(lines)
