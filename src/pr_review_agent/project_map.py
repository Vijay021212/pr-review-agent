"""The project map: a short overview of the repository, built by code (FR-5, design 5).

The explore stage shows it to the model before the first step, so the model knows the
layout and where to look. It holds the folder tree, the start of the README, the project
rules file, and files that usually explain how a project is set up or started.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from pr_review_agent.tools import SKIPPED_FOLDERS

RULES_FILE = ".pr-review-agent.toml"
MAX_TREE_ENTRIES = 300
README_LINES = 100
KEY_FILE_LINES = 60
MAX_KEY_FILES = 8
# Configuration and entry-point files, in the order they're shown (design section 5).
KEY_FILE_NAMES = (
    "pyproject.toml",
    "setup.cfg",
    "package.json",
    "main.py",
    "__main__.py",
    "cli.py",
    "app.py",
    "settings.py",
    "config.py",
)
README_NAMES = ("README.md", "README.rst", "README.txt", "README")
# Folders that hold installed or generated code; the tree leaves them out like the tools do.
TREE_SKIPPED = SKIPPED_FOLDERS | {"dist", "build", ".tox", ".idea", ".vscode"}


def build_project_map(root: Path) -> str:
    sections = [f"## Folder tree\n{_tree(root)}"]
    readme = next((root / name for name in README_NAMES if (root / name).is_file()), None)
    if readme is not None:
        sections.append(
            f"## {readme.name} (first {README_LINES} lines)\n{_head(readme, README_LINES)}"
        )
    if (root / RULES_FILE).is_file():
        sections.append(
            f"## {RULES_FILE} (the project's review rules)\n{_head(root / RULES_FILE, 100)}"
        )
    for path in _key_files(root):
        relative = path.relative_to(root).as_posix()
        sections.append(
            f"## {relative} (first {KEY_FILE_LINES} lines)\n{_head(path, KEY_FILE_LINES)}"
        )
    return "\n\n".join(sections)


def _walk(root: Path) -> Iterator[tuple[Path, list[str]]]:
    """Each folder (depth first, sorted) with its file names, skipping hidden and installed
    folders, never through a link."""
    for folder, subfolders, files in os.walk(root, followlinks=False):
        subfolders[:] = sorted(
            name for name in subfolders if name not in TREE_SKIPPED and not name.startswith(".")
        )
        yield Path(folder), sorted(files)


def _tree(root: Path) -> str:
    """Folders and files, indented, in path order, up to the entry limit."""
    entries: list[str] = []
    for folder, files in _walk(root):
        depth = len(folder.relative_to(root).parts)
        if depth:
            entries.append("  " * (depth - 1) + folder.name + "/")
        entries += ["  " * depth + name for name in files]
    lines = entries[:MAX_TREE_ENTRIES]
    if len(entries) > MAX_TREE_ENTRIES:
        lines.append(f"... and {len(entries) - MAX_TREE_ENTRIES} more entries")
    return "\n".join(lines) or "(empty repository)"


def _key_files(root: Path) -> list[Path]:
    """Configuration and entry-point files, shallowest first, at most `MAX_KEY_FILES`."""
    found = [
        folder / name
        for folder, files in _walk(root)
        for name in files
        if name in KEY_FILE_NAMES and not (folder / name).is_symlink()
    ]
    found.sort(
        key=lambda path: (
            len(path.relative_to(root).parts),
            KEY_FILE_NAMES.index(path.name),
            path.as_posix(),
        )
    )
    return found[:MAX_KEY_FILES]


def _head(path: Path, lines: int) -> str:
    text = path.read_bytes().decode("utf-8", errors="replace").splitlines()
    shown = "\n".join(text[:lines])
    return shown + (f"\n... {len(text) - lines} more lines" if len(text) > lines else "")
