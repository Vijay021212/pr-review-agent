"""Reading the evaluation's hand-written YAML files: each case's `case.yaml` and the readings.

People write these files by hand, so a mistake in one names the file and says what's wrong
in it and where (FR-16), instead of stopping with an error that names neither.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from pr_review_agent.domain import SetupError


class _TextLoader(yaml.SafeLoader):
    """The safe loader, except that text that looks like a date stays text.

    Every field of these files that could hold one, like a problem's marker copied from a
    line of code, is text, and an impossible date like 2026-02-29 would stop the loading.
    """


_TextLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


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
        return _load(text)
    except yaml.YAMLError as error:
        raise SetupError(f"{what} {path} isn't valid YAML: {_problem(error)}.", fix) from None
    except ValueError as error:  # a value YAML reads as a number but can't convert, like 0x_
        raise SetupError(f"{what} {path} isn't valid YAML: {error}.", fix) from None


def _load(text: str) -> Any:
    loader = _TextLoader(text)  # checks the text for characters YAML doesn't allow
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _problem(error: yaml.YAMLError) -> str:
    """What the parser found and where, with where the part it was reading starts: for
    example "while scanning a simple key (line 2, column 3): could not find expected ':'
    (line 3, column 3)". The start is usually where the mistake is."""
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
        problem = error.problem or "it can't be read"
        what = f"{problem} ({_where(error.problem_mark)})"
        if error.context and error.context_mark is not None:
            return f"{error.context} ({_where(error.context_mark)}): {what}"
        return what
    # Other errors, like a control character in the text, name the parsed text "<unicode
    # string>", which means nothing to the reader.
    return " ".join(_STREAM_NAME.sub(" at ", str(error)).split())


def _where(mark: yaml.error.Mark) -> str:
    return f"line {mark.line + 1}, column {mark.column + 1}"


_STREAM_NAME = re.compile(r'\s*in "<unicode string>",\s*')
