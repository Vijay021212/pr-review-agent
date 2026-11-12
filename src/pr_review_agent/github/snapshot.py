"""The repository snapshot: the reviewed commit's files, unpacked into a temporary folder.

The explore stage reads related code from here (FR-5), always at the reviewed commit
(FR-19). Pull request content is untrusted (NFR-5), so the archive is unpacked with
`tarfile`'s "data" filter, which refuses absolute paths, `..` paths, links that point
outside the folder, and device files. Every entry is checked before anything is written,
so an unsafe archive is never partly unpacked. The files are made read-only, and the
folder is removed when the review ends, even after an error.
"""

from __future__ import annotations

import os
import stat
import tarfile
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pr_review_agent.domain import Deadline, GitHub, PullRequest, ReviewIncomplete

MAX_SNAPSHOT_BYTES = 300 * 1024 * 1024
"""The largest compressed download accepted: more than any normal repository needs, and a
guard against filling the disk with a hostile or huge archive."""


@contextmanager
def snapshot(github: GitHub, pull_request: PullRequest, deadline: Deadline) -> Iterator[Path]:
    """The snapshot's root folder for the duration of the `with` block, removed after."""
    with tempfile.TemporaryDirectory(prefix="pr-review-agent-") as folder:
        yield github.download_snapshot(pull_request, Path(folder), deadline)


def write_limited(chunks: Iterable[bytes], path: Path, limit: int) -> None:
    """Write the downloaded bytes to `path`, stopping the review if they pass `limit`."""
    written = 0
    with path.open("wb") as file:
        for chunk in chunks:
            written += len(chunk)
            if written > limit:
                raise ReviewIncomplete(
                    f"the repository snapshot is larger than {limit // (1024 * 1024)} MB, "
                    "so it wasn't downloaded"
                )
            file.write(chunk)


def unpack(archive: Path, target: Path) -> Path:
    """Unpack the tarball safely into `target` and return the repository's root folder.

    GitHub's tarballs hold one top folder named after the repository and commit, so its
    contents are the root.
    """
    target.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:  # check every entry before writing any
                tarfile.data_filter(member, str(target))
            tar.extractall(target, members=members, filter="data")
    except tarfile.FilterError as error:
        raise ReviewIncomplete(
            f"the repository snapshot holds an unsafe path ({error}), so it wasn't unpacked"
        ) from error
    except (tarfile.TarError, EOFError) as error:
        raise ReviewIncomplete(f"the repository snapshot couldn't be read ({error})") from error
    entries = list(target.iterdir())
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else target
    _make_read_only(root)
    return root


def _make_read_only(root: Path) -> None:
    """Nothing in the review should change the snapshot; read-only files make that visible."""
    for folder, _, files in os.walk(root):
        for name in files:
            path = Path(folder, name)
            if not path.is_symlink():
                path.chmod(stat.S_IREAD)
