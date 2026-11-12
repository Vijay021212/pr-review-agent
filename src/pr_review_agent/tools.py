"""The explore stage's read-only tools: list a folder, open a file, search (design 5).

They're plain Python functions over the repository snapshot, and the model can only pick
one and give it a path or text. The security boundary (NFR-5) lives here:

- every path is resolved and must stay inside the snapshot;
- a symbolic link anywhere on the path is refused, never followed;
- nothing here writes, runs code or reaches the network.

A refused or failed call returns a short message for the model instead of stopping the
review, so the agent can correct itself and carry on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pr_review_agent.domain import ToolAction

MAX_ENTRIES = 200
MAX_LINES = 300
MAX_MATCHES = 50
MAX_LINE_CHARS = 200  # a matching line is shortened to this in search results
MAX_SEARCH_FILE_BYTES = 1024 * 1024  # bigger files are data or generated code, not worth searching
MAX_OPEN_FILE_BYTES = 5 * 1024 * 1024
SKIPPED_FOLDERS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache"}
)


@dataclass(frozen=True)
class ToolResult:
    output: str  # what the model reads
    summary: str  # a few words for the summary and the run log, for example "14 matches"


class ToolRefused(Exception):
    """A call the tools won't carry out; the message tells the model why."""


class ReadOnlyTools:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def run(self, action: ToolAction, target: str = "", start_line: int = 1) -> ToolResult:
        """Carry out one tool call. A refused or failed call becomes a message, not an error."""
        try:
            if action is ToolAction.LIST_DIR:
                return self.list_dir(target)
            if action is ToolAction.OPEN_FILE:
                return self.open_file(target, start_line)
            if action is ToolAction.SEARCH:
                return self.search(target)
            raise ToolRefused(f"{action} isn't a tool")
        except ToolRefused as refused:
            return ToolResult(output=f"Refused: {refused}", summary=f"refused: {refused}")

    def list_dir(self, path: str = "") -> ToolResult:
        folder = self._resolve(path)
        if not folder.is_dir():
            raise ToolRefused(f"{_shown(path)} isn't a folder")
        entries = sorted(folder.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name))
        lines = [_entry_line(entry) for entry in entries[:MAX_ENTRIES]]
        if len(entries) > MAX_ENTRIES:
            lines.append(f"... and {len(entries) - MAX_ENTRIES} more entries")
        return ToolResult(
            output="\n".join(lines) or "(empty folder)", summary=f"{len(entries)} entries"
        )

    def open_file(self, path: str, start_line: int = 1) -> ToolResult:
        file = self._resolve(path)
        if not file.is_file():
            raise ToolRefused(f"{_shown(path)} isn't a file")
        if file.stat().st_size > MAX_OPEN_FILE_BYTES:
            raise ToolRefused(f"{_shown(path)} is too large to open; search it instead")
        text = _read_text(file)
        if text is None:
            raise ToolRefused(f"{_shown(path)} is a binary file")
        lines = text.splitlines()
        start = max(1, start_line)
        if lines and start > len(lines):
            raise ToolRefused(f"{_shown(path)} has only {len(lines)} lines")
        end = min(len(lines), start + MAX_LINES - 1)
        numbered = [f"{number:>6}  {lines[number - 1]}" for number in range(start, end + 1)]
        if end < len(lines):
            numbered.append(
                f"... lines {end + 1}-{len(lines)} not shown; open again with start_line={end + 1}"
            )
        summary = f"lines {start}-{end} of {len(lines)}" if lines else "empty file"
        return ToolResult(output="\n".join(numbered) or "(empty file)", summary=summary)

    def search(self, text: str) -> ToolResult:
        if not text.strip():
            raise ToolRefused("search needs some text to look for")
        matches: list[str] = []
        for file in self._files():
            content = _read_text(file, MAX_SEARCH_FILE_BYTES)
            if content is None:
                continue
            for number, line in enumerate(content.splitlines(), start=1):
                if text in line:
                    shown = line.strip()[:MAX_LINE_CHARS]
                    matches.append(f"{self._relative(file)}:{number}: {shown}")
                    if len(matches) == MAX_MATCHES:
                        matches.append(f"... stopped at {MAX_MATCHES} matches")
                        return ToolResult("\n".join(matches), f"{MAX_MATCHES}+ matches")
        if not matches:
            return ToolResult(output=f"No matches for {text!r}.", summary="no matches")
        return ToolResult("\n".join(matches), f"{len(matches)} matches")

    def _resolve(self, path: str) -> Path:
        """The real path for a repository path, refusing anything outside or through a link."""
        clean = path.strip().replace("\\", "/").strip("/")
        parts = PurePosixPath(clean).parts if clean not in ("", ".") else ()
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ToolRefused(f"{_shown(path)} goes through a link, which isn't followed")
        resolved = current.resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolRefused(f"{_shown(path)} is outside the repository")
        if not resolved.exists():
            raise ToolRefused(f"{_shown(path)} doesn't exist")
        return resolved

    def _files(self) -> list[Path]:
        """Every searchable file, in path order, never through a link."""
        found = []
        for folder, subfolders, files in os.walk(self.root, followlinks=False):
            subfolders[:] = sorted(name for name in subfolders if name not in SKIPPED_FOLDERS)
            found += [Path(folder, name) for name in sorted(files)]
        return [path for path in found if not path.is_symlink()]

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def _entry_line(entry: Path) -> str:
    if entry.is_symlink():
        return f"{entry.name}  (link, not followed)"
    return f"{entry.name}/" if entry.is_dir() else entry.name


def _shown(path: str) -> str:
    return f"'{path}'" if path.strip() else "the repository root"


def _read_text(file: Path, limit: int | None = None) -> str | None:
    """The file's text, or None for a binary file or one over `limit` bytes."""
    if limit is not None and file.stat().st_size > limit:
        return None
    data = file.read_bytes()
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")
