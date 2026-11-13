"""Tests for the repository snapshot: safe unpacking, forks, limits and clean-up (NFR-5)."""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import httpx
import pytest

from pr_review_agent.domain import Deadline, PullRequest, ReviewIncomplete, SetupError
from pr_review_agent.github import snapshot as snapshot_module
from pr_review_agent.github.rest import GitHubRest
from pr_review_agent.github.snapshot import snapshot, unpack, write_limited
from tests.builders import SHA, make_pull_request
from tests.fakes import FakeGitHub

TOP = f"octo-shop-{SHA[:7]}"


def tarball(*entries: tarfile.TarInfo | tuple[str, str]) -> bytes:
    """A .tar.gz with the given entries: (path, text) for files, or a prepared TarInfo."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for entry in entries:
            if isinstance(entry, tarfile.TarInfo):
                tar.addfile(entry)
                continue
            path, text = entry
            data = text.encode()
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def link(name: str, target: str, kind: bytes = tarfile.SYMTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info


def write_archive(folder: Path, data: bytes) -> Path:
    archive = folder / "snapshot.tar.gz"
    archive.write_bytes(data)
    return archive


# --- Unpacking -------------------------------------------------------------------------


def test_the_top_folder_becomes_the_root(tmp_path: Path) -> None:
    data = tarball((f"{TOP}/README.md", "# Shop"), (f"{TOP}/src/cart.py", "def total(): ..."))
    root = unpack(write_archive(tmp_path, data), tmp_path / "repository")
    assert root == tmp_path / "repository" / TOP
    assert (root / "src" / "cart.py").read_text() == "def total(): ..."
    assert (root / "README.md").exists()


def test_an_archive_without_one_top_folder_is_its_own_root(tmp_path: Path) -> None:
    root = unpack(write_archive(tmp_path, tarball(("a.py", "1"), ("b.py", "2"))), tmp_path / "r")
    assert root == tmp_path / "r"
    assert sorted(path.name for path in root.iterdir()) == ["a.py", "b.py"]


def test_unpacked_files_are_read_only(tmp_path: Path) -> None:
    root = unpack(write_archive(tmp_path, tarball((f"{TOP}/a.py", "x = 1"))), tmp_path / "r")
    assert not os.access(root / "a.py", os.W_OK)


@pytest.mark.parametrize(
    "entry",
    [
        ("../escape.py", "evil"),
        (f"{TOP}/../../escape.py", "evil"),
        link(f"{TOP}/outside", "../../outside"),
        link(f"{TOP}/abs", "/etc/passwd"),
        link(f"{TOP}/hard", "/etc/passwd", tarfile.LNKTYPE),
    ],
    ids=["parent folder", "parent of the top folder", "link leaving", "absolute link", "hard link"],
)
def test_paths_that_escape_the_folder_are_refused(
    tmp_path: Path, entry: tarfile.TarInfo | tuple[str, str]
) -> None:
    data = tarball((f"{TOP}/good.py", "ok"), entry)
    with pytest.raises(ReviewIncomplete, match="unsafe path"):
        unpack(write_archive(tmp_path, data), tmp_path / "r")
    # Nothing was written, not even the safe file before the unsafe one.
    assert list((tmp_path / "r").iterdir()) == []
    assert not (tmp_path / "escape.py").exists()


def test_a_link_inside_the_snapshot_is_allowed(tmp_path: Path) -> None:
    """A link to another file in the repository is safe. (Windows without symbolic-link
    rights unpacks it as a copy instead, which is just as safe.)"""
    data = tarball((f"{TOP}/a.py", "x = 1"), link(f"{TOP}/alias.py", "a.py"))
    root = unpack(write_archive(tmp_path, data), tmp_path / "r")
    assert (root / "alias.py").read_text() == "x = 1"


def test_paths_that_stay_inside_are_kept_inside(tmp_path: Path) -> None:
    """An absolute path loses its leading "/", and a ".." that stays inside is harmless."""
    data = tarball(("/etc/evil.py", "1"), ("pkg/../inside.py", "2"))
    root = unpack(write_archive(tmp_path, data), tmp_path / "r")
    assert (root / "etc" / "evil.py").read_text() == "1"
    assert (root / "inside.py").read_text() == "2"


def test_a_damaged_archive_stops_the_review(tmp_path: Path) -> None:
    with pytest.raises(ReviewIncomplete, match="couldn't be read"):
        unpack(write_archive(tmp_path, b"not a tarball"), tmp_path / "r")


def test_downloads_over_the_limit_are_stopped(tmp_path: Path) -> None:
    with pytest.raises(ReviewIncomplete, match="larger than 1 MB"):
        write_limited([b"x" * 600_000, b"x" * 600_000], tmp_path / "a", 1024 * 1024)
    write_limited([b"x" * 10], tmp_path / "b", 1024 * 1024)
    assert (tmp_path / "b").read_bytes() == b"x" * 10


# --- Downloading from GitHub -----------------------------------------------------------


def github_serving(handler: httpx.MockTransport) -> GitHubRest:
    return GitHubRest("ghp_test", client=httpx.Client(transport=handler), sleep=lambda s: None)


def fork_pull_request() -> PullRequest:
    return make_pull_request(head_owner="contributor", head_repo="shop-fork")


def test_the_fork_is_downloaded_at_the_head_commit_without_leaking_the_token(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    data = tarball((f"{TOP}/src/cart.py", "def total(): ..."))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://codeload.github.com/x.tgz"})
        return httpx.Response(200, content=data)

    root = github_serving(httpx.MockTransport(handler)).download_snapshot(
        fork_pull_request(), tmp_path, Deadline(60)
    )
    assert (root / "src" / "cart.py").read_text() == "def total(): ..."
    api, download = requests
    assert api.url.path == f"/repos/contributor/shop-fork/tarball/{SHA}"
    assert api.headers["Authorization"] == "Bearer ghp_test"
    assert download.url.host == "codeload.github.com"
    assert "Authorization" not in download.headers
    assert not (tmp_path / "snapshot.tar.gz").exists()  # the archive itself is removed


def test_a_temporary_error_is_retried(tmp_path: Path) -> None:
    replies = [httpx.Response(502), httpx.Response(200, content=tarball(("a.py", "1")))]
    github = github_serving(httpx.MockTransport(lambda request: replies.pop(0)))
    root = github.download_snapshot(make_pull_request(), tmp_path, Deadline(60))
    assert (root / "a.py").exists()


def test_a_missing_repository_is_a_setup_error(tmp_path: Path) -> None:
    github = github_serving(
        httpx.MockTransport(lambda request: httpx.Response(404, json={"message": "Not Found"}))
    )
    with pytest.raises(SetupError, match="GitHub couldn't find the repository snapshot"):
        github.download_snapshot(make_pull_request(), tmp_path, Deadline(60))


def test_a_huge_download_is_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pr_review_agent.github.rest.MAX_SNAPSHOT_BYTES", 10)
    github = github_serving(
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 100))
    )
    with pytest.raises(ReviewIncomplete, match="larger than 0 MB"):
        github.download_snapshot(make_pull_request(), tmp_path, Deadline(60))
    assert not (tmp_path / "snapshot.tar.gz").exists()


# --- Lifetime --------------------------------------------------------------------------


def test_the_snapshot_is_removed_after_the_review(tmp_path: Path) -> None:
    github = FakeGitHub(repository={"src/cart.py": "x = 1"})
    with snapshot(github, make_pull_request(), Deadline(60)) as root:
        assert (root / "src" / "cart.py").exists()
        kept = root
    assert not kept.exists()
    assert github.snapshots == [SHA]


def test_the_snapshot_is_removed_even_after_an_error(tmp_path: Path) -> None:
    github = FakeGitHub(repository={"a.py": "1"})
    seen: list[Path] = []

    def review_that_fails() -> None:
        with snapshot(github, make_pull_request(), Deadline(60)) as root:
            seen.append(root)
            raise RuntimeError("a stage failed")

    with pytest.raises(RuntimeError):
        review_that_fails()
    assert seen
    assert not seen[0].exists()


def test_read_only_files_are_removed_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows a read-only file can't simply be deleted; the clean-up must still work."""
    data = tarball((f"{TOP}/a.py", "1"))

    class TarballGitHub(FakeGitHub):
        def download_snapshot(
            self, pull_request: PullRequest, folder: Path, deadline: Deadline
        ) -> Path:
            return unpack(write_archive(folder, data), folder / "repository")

    with snapshot(TarballGitHub(), make_pull_request(), Deadline(60)) as root:
        assert not os.access(root / "a.py", os.W_OK)
        kept = root
    assert not kept.exists()
    assert snapshot_module.MAX_SNAPSHOT_BYTES == 300 * 1024 * 1024
