"""Find secrets in text and mask them, so the agent never repeats one (FR-21).

Every text that leaves the program goes through `Masker`: review comments, the summary,
terminal output and the run log (design section 8). A masked secret keeps a recognisable
prefix and its last 4 characters, for example `sk-...a1b2`, so a person can still tell
which key was meant without the value being exposed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

# Values shorter than this are masked completely: their last 4 characters would give away
# too much of them.
_KEEP_LAST_FROM = 16


@dataclass(frozen=True)
class SecretMatch:
    """One secret found in a text: what kind it is, and where (start and end offsets)."""

    kind: str
    start: int
    end: int
    masked: str  # what replaces text[start:end]


def _keep_prefix_and_last_four(prefix: str, value: str) -> str:
    """`sk-` + `...` + last 4, or `sk-****` when the value is too short to show any of it."""
    return f"{prefix}...{value[-4:]}" if len(value) >= _KEEP_LAST_FROM else f"{prefix}****"


@dataclass(frozen=True)
class _Pattern:
    kind: str
    regex: re.Pattern[str]
    mask: Callable[[re.Match[str]], str]


def _prefixed(kind: str, regex: str) -> _Pattern:
    """A token that starts with a fixed prefix (group `prefix`) followed by the secret part."""
    return _Pattern(
        kind,
        re.compile(regex),
        lambda m: _keep_prefix_and_last_four(m["prefix"], m[0]),
    )


# Checked in this order; where two patterns overlap, the earlier one wins.
_PATTERNS = (
    _Pattern(
        "private key",
        re.compile(
            r"(?P<begin>-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----)"
            r"[\s\S]*?(?:(?P<end>-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----)|\Z)"
        ),
        # The BEGIN and END lines stay, so masking an already masked key changes nothing.
        lambda m: f"{m['begin']}...masked...{m['end'] or ''}",
    ),
    _prefixed("Anthropic API key", r"\b(?P<prefix>sk-ant-)[A-Za-z0-9_-]{20,}"),
    _prefixed("OpenAI-style API key", r"\b(?P<prefix>sk-)(?!ant-)[A-Za-z0-9_-]{20,}"),
    _prefixed(
        "GitHub token",
        r"\b(?P<prefix>gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,255}\b",
    ),
    _prefixed("AWS access key", r"\b(?P<prefix>AKIA|ASIA)[0-9A-Z]{16}\b"),
    # password = "...", "api_key": '...', TOKEN='...': the name and quotes stay, the value
    # is masked. Only quoted values of 4 or more characters count, so code like
    # `password = input()` or `token = ""` is left alone; values already masked
    # ("...a1b2", "****") are skipped, so masking twice changes nothing.
    _Pattern(
        "password or key in code",
        re.compile(
            r"(?i)(?P<name>\b(?:password|passwd|pwd|secret|api_?key|access_?key|token)\b"
            r"[\"']?\s*[:=]\s*)(?P<quote>[\"'])(?!\.\.\.|\*{4})"
            r"(?P<value>[^\"'\s]{4,})(?P=quote)"
        ),
        lambda m: (
            f"{m['name']}{m['quote']}{_keep_prefix_and_last_four('', m['value'])}{m['quote']}"
        ),
    ),
)


def find_secrets(text: str) -> list[SecretMatch]:
    """Every secret in `text`, in order, without overlaps."""
    matches: list[SecretMatch] = []
    for pattern in _PATTERNS:
        for found in pattern.regex.finditer(text):
            start, end = found.span()
            if not any(start < other.end and other.start < end for other in matches):
                matches.append(SecretMatch(pattern.kind, start, end, pattern.mask(found)))
    return sorted(matches, key=lambda match: match.start)


class Masker:
    """Masks the known patterns, plus exact values it's told about.

    The exact values are the user's own GitHub token and API key: they must never appear
    in output either, even if their format doesn't match a known pattern.
    """

    def __init__(self, known_values: Iterable[str] = ()) -> None:
        # Longest first, so a value that contains another is masked as a whole.
        self._known = sorted({value for value in known_values if value}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for value in self._known:
            text = text.replace(value, _keep_prefix_and_last_four("", value))
        parts, position = [], 0
        for match in find_secrets(text):
            parts += [text[position : match.start], match.masked]
            position = match.end
        parts.append(text[position:])
        return "".join(parts)


mask = Masker()
"""Masks the known patterns; use a `Masker` with known values when tokens are configured."""
