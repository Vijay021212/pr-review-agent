"""Tests for the read-only explore tools: limits, and the security boundary (NFR-5)."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from pr_review_agent import tools
from pr_review_agent.domain import ToolAction
from pr_review_agent.tools import MAX_ENTRIES, MAX_LINES, MAX_MATCHES, ReadOnlyTools


def make_repo(root: Path, files: dict[str, str | bytes]) -> ReadOnlyTools:
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return ReadOnlyTools(root)


def make_link(link: Path, target: Path) -> None:
    """A symbolic link, or skip the test where this computer may not create one."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("creating symbolic links isn't allowed here")


@pytest.fixture
def repo(tmp_path: Path) -> ReadOnlyTools:
    root = tmp_path / "repo"
    return make_repo(
        root,
        {
            "README.md": "# Shop\n",
            "src/cart.py": "def total(items):\n    return average(items)\n",
            "src/stats.py": "def average(numbers):\n    return sum(numbers) / len(numbers)\n",
            "tests/test_stats.py": "from src.stats import average\n",
            "assets/logo.png": b"\x89PNG\x00\x00binary",
            "node_modules/lib/index.js": "average(",
        },
    )


# --- list_dir --------------------------------------------------------------------------


def test_list_dir_shows_folders_first(repo: ReadOnlyTools) -> None:
    result = repo.list_dir("")
    assert result.output.splitlines() == [
        "assets/", "node_modules/", "src/", "tests/", "README.md",
    ]  # fmt: skip
    assert result.summary == "5 entries"
    assert repo.list_dir("src").output == "cart.py\nstats.py"
    assert repo.list_dir("./src/").output == "cart.py\nstats.py"


def test_list_dir_stops_at_its_limit(tmp_path: Path) -> None:
    tools_ = make_repo(tmp_path, {f"f{n:03}.py": "" for n in range(MAX_ENTRIES + 5)})
    lines = tools_.list_dir("").output.splitlines()
    assert len(lines) == MAX_ENTRIES + 1
    assert lines[-1] == "... and 5 more entries"


def test_an_empty_folder_says_so(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert ReadOnlyTools(tmp_path).list_dir("empty").output == "(empty folder)"


# --- open_file -------------------------------------------------------------------------


def test_open_file_numbers_its_lines(repo: ReadOnlyTools) -> None:
    result = repo.open_file("src/stats.py")
    assert result.output == (
        "     1  def average(numbers):\n     2      return sum(numbers) / len(numbers)"
    )
    assert result.summary == "lines 1-2 of 2"


def test_open_file_shows_at_most_300_lines_and_says_how_to_go_on(tmp_path: Path) -> None:
    tools_ = make_repo(tmp_path, {"big.py": "\n".join(f"x{n} = {n}" for n in range(1, 701))})
    first = tools_.open_file("big.py")
    lines = first.output.splitlines()
    assert len(lines) == MAX_LINES + 1
    assert lines[-1] == "... lines 301-700 not shown; open again with start_line=301"
    second = tools_.open_file("big.py", 301)
    assert second.output.splitlines()[0] == "   301  x301 = 301"
    assert second.summary == "lines 301-600 of 700"


def test_open_file_past_the_end_is_refused(repo: ReadOnlyTools) -> None:
    assert repo.run(ToolAction.OPEN_FILE, "src/stats.py", 9).output == (
        "Refused: 'src/stats.py' has only 2 lines"
    )


def test_an_empty_file(tmp_path: Path) -> None:
    result = make_repo(tmp_path, {"empty.py": ""}).open_file("empty.py")
    assert (result.output, result.summary) == ("(empty file)", "empty file")


@pytest.mark.parametrize(
    ("action", "target", "message"),
    [
        (ToolAction.OPEN_FILE, "assets/logo.png", "'assets/logo.png' is a binary file"),
        (ToolAction.OPEN_FILE, "src", "'src' isn't a file"),
        (ToolAction.LIST_DIR, "README.md", "'README.md' isn't a folder"),
        (ToolAction.OPEN_FILE, "src/missing.py", "'src/missing.py' doesn't exist"),
        (ToolAction.SEARCH, "  ", "search needs some text to look for"),
        (ToolAction.DONE, "", "done isn't a tool"),
    ],
)
def test_impossible_calls_become_messages_for_the_model(
    repo: ReadOnlyTools, action: ToolAction, target: str, message: str
) -> None:
    result = repo.run(action, target)
    assert result.output == f"Refused: {message}"
    assert result.summary == f"refused: {message}"


def test_a_very_large_file_isnt_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "MAX_OPEN_FILE_BYTES", 10)
    result = make_repo(tmp_path, {"data.py": "x = 1\n" * 10}).run(ToolAction.OPEN_FILE, "data.py")
    assert result.output == "Refused: 'data.py' is too large to open; search it instead"


# --- search ----------------------------------------------------------------------------


def test_search_finds_plain_text_with_file_and_line(repo: ReadOnlyTools) -> None:
    result = repo.search("average(")
    assert result.output.splitlines() == [
        "src/cart.py:2: return average(items)",
        "src/stats.py:1: def average(numbers):",
    ]
    assert result.summary == "2 matches"


def test_search_is_plain_text_not_a_pattern(repo: ReadOnlyTools) -> None:
    assert repo.search("sum(numbers) / len").summary == "1 matches"
    assert repo.search(".*").summary == "no matches"


def test_search_says_when_nothing_matches(repo: ReadOnlyTools) -> None:
    assert repo.search("discount").output == "No matches for 'discount'."


def test_search_stops_at_its_limit(tmp_path: Path) -> None:
    tools_ = make_repo(tmp_path, {"a.py": "hit\n" * (MAX_MATCHES + 20)})
    result = tools_.search("hit")
    assert len(result.output.splitlines()) == MAX_MATCHES + 1
    assert result.output.endswith(f"... stopped at {MAX_MATCHES} matches")
    assert result.summary == f"{MAX_MATCHES}+ matches"


def test_search_skips_binary_large_and_installed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "MAX_SEARCH_FILE_BYTES", 100)
    tools_ = make_repo(
        tmp_path,
        {
            "a.py": "needle",
            "image.bin": b"needle\x00",
            "big.py": "needle" + "x" * 200,
            "node_modules/x.js": "needle",
            ".git/config": "needle",
        },
    )
    assert tools_.search("needle").output == "a.py:1: needle"


def test_long_matching_lines_are_shortened(tmp_path: Path) -> None:
    result = make_repo(tmp_path, {"a.py": "needle" + "x" * 500}).search("needle")
    assert len(result.output) == len("a.py:1: ") + 200


# --- The security boundary -------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["..", "../outside.txt", "src/../../outside.txt", "C:/Windows/win.ini", "..\\outside.txt"],
)
def test_paths_outside_the_repository_are_refused(repo: ReadOnlyTools, path: str) -> None:
    (repo.root.parent / "outside.txt").write_text("secret", encoding="utf-8")
    result = repo.run(ToolAction.OPEN_FILE, path)
    assert result.output.startswith("Refused:")
    assert "secret" not in result.output


def test_an_absolute_path_is_read_inside_the_repository(repo: ReadOnlyTools) -> None:
    assert repo.open_file("/src/stats.py").summary == "lines 1-2 of 2"


def test_a_link_to_a_file_outside_is_never_followed(repo: ReadOnlyTools) -> None:
    outside = repo.root.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    make_link(repo.root / "src" / "leak.py", outside)
    assert repo.run(ToolAction.OPEN_FILE, "src/leak.py").output == (
        "Refused: 'src/leak.py' goes through a link, which isn't followed"
    )
    assert "secret" not in repo.search("secret").output
    assert "leak.py  (link, not followed)" in repo.list_dir("src").output


def test_a_link_to_a_folder_is_never_followed(repo: ReadOnlyTools) -> None:
    outside = repo.root.parent / "elsewhere"
    outside.mkdir()
    (outside / "secret.py").write_text("password", encoding="utf-8")
    make_link(repo.root / "linked", outside)
    assert repo.run(ToolAction.LIST_DIR, "linked").output.startswith("Refused:")
    assert repo.run(ToolAction.OPEN_FILE, "linked/secret.py").output.startswith("Refused:")
    assert repo.search("password").summary == "no matches"


def test_the_tools_cant_write_run_code_or_reach_the_network() -> None:
    """Reads the module's own source: no import or call that could change anything."""
    tree = ast.parse(Path(tools.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }
    forbidden_modules = {"subprocess", "socket", "httpx", "urllib", "http", "shutil", "ctypes"}
    assert not imported & forbidden_modules
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    forbidden_calls = {
        "write_text", "write_bytes", "unlink", "mkdir", "rmdir", "chmod", "rename", "touch",
        "remove", "system", "popen", "exec", "eval", "open", "symlink_to",
    }  # fmt: skip
    assert not called & forbidden_calls
    assert os.path.basename(tools.__file__) == "tools.py"
