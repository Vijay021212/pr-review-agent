"""Tests for the project map built by code (FR-5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pr_review_agent import project_map
from pr_review_agent.project_map import build_project_map


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    return root


def test_the_map_holds_the_tree_readme_rules_and_key_files(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "README.md": "# Shop\nA small shop.\n",
            ".pr-review-agent.toml": 'ignore = ["docs/**"]\n',
            "pyproject.toml": '[project]\nname = "shop"\n',
            "src/shop/cli.py": "def main(): ...\n",
            "src/shop/cart.py": "def total(): ...\n",
            "tests/test_cart.py": "def test_total(): ...\n",
        },
    )
    text = build_project_map(root)
    assert text.startswith("## Folder tree\n")
    assert "src/\n  shop/\n    cart.py\n    cli.py" in text
    assert "## README.md (first 100 lines)\n# Shop\nA small shop." in text
    assert '## .pr-review-agent.toml (the project\'s review rules)\nignore = ["docs/**"]' in text
    assert '## pyproject.toml (first 60 lines)\n[project]\nname = "shop"' in text
    assert "## src/shop/cli.py (first 60 lines)\ndef main(): ..." in text
    # The shallowest key file comes first.
    assert text.index("## pyproject.toml") < text.index("## src/shop/cli.py")


def test_hidden_and_installed_folders_are_left_out_of_the_tree(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "app.py": "",
            ".git/config": "",
            ".github/workflows/ci.yml": "",
            "node_modules/x/index.js": "",
            "dist/shop.whl": "",
            "src/__pycache__/a.pyc": "",
        },
    )
    tree = build_project_map(root).split("\n\n")[0]
    assert "node_modules" not in tree
    assert ".git" not in tree
    assert "dist" not in tree
    assert "__pycache__" not in tree
    assert "app.py" in tree


def test_the_tree_stops_at_its_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(project_map, "MAX_TREE_ENTRIES", 3)
    root = make_repo(tmp_path, {f"f{n}.py": "" for n in range(5)})
    tree = build_project_map(root).split("\n\n")[0]
    assert tree.splitlines()[1:] == ["f0.py", "f1.py", "f2.py", "... and 2 more entries"]


def test_long_files_are_shortened(tmp_path: Path) -> None:
    long_readme = "\n".join(f"line {n}" for n in range(1, 151))
    root = make_repo(tmp_path, {"README.md": long_readme})
    text = build_project_map(root)
    assert "line 100\n... 50 more lines" in text
    assert "line 101" not in text


def test_at_most_eight_key_files(tmp_path: Path) -> None:
    files = {f"pkg{n}/config.py": "" for n in range(12)}
    text = build_project_map(make_repo(tmp_path, files))
    assert text.count("/config.py (first 60 lines)") == 8


def test_a_bare_repository_has_only_a_tree(tmp_path: Path) -> None:
    assert build_project_map(tmp_path) == "## Folder tree\n(empty repository)"
    make_repo(tmp_path, {"notes.txt": "hello"})
    assert build_project_map(tmp_path) == "## Folder tree\nnotes.txt"
