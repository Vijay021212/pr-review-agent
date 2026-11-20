"""Reading the evaluation's hand-written YAML files: each case's `case.yaml` and the readings.

People write these files by hand, so a mistake in one names the file and says what's wrong
in it (FR-16), instead of stopping with an error that names neither.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from pr_review_agent.domain import SetupError


def read_yaml(path: Path, *, what: str, fix: str) -> Any:
    """The YAML document in `path`, or None when it's empty.

    `what` names the file in an error, for example "The readings file", and `fix` says how
    to correct a file that isn't valid YAML.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SetupError(f"{what} {path} isn't UTF-8 text.", "Save it as UTF-8.") from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SetupError(f"{what} {path} isn't valid YAML: {_problem(error)}.", fix) from None


def _problem(error: yaml.YAMLError) -> str:
    """What the parser found and where, for example "mapping values are not allowed here
    (line 1, column 5)"."""
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
        mark = error.problem_mark
        what = error.problem or error.context or "it can't be read"
        return f"{what} (line {mark.line + 1}, column {mark.column + 1})"
    # Other errors, like a control character in the text, name the parsed text "<unicode
    # string>", which means nothing to the reader.
    return " ".join(_STREAM_NAME.sub(" at ", str(error)).split())


_STREAM_NAME = re.compile(r'\s*in "<unicode string>",\s*')
