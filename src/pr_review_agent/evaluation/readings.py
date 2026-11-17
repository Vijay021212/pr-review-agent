"""The reading of each finding (requirements 7.1), recorded in eval/readings.yaml.

A finding catches a planted problem only when it also describes that problem, and a
finding that matches none is either a real problem or a false alarm. Code can't tell
which; a person reads the finding and records the verdict with the reason:

    - case: l3-c-cache-not-cleared
      finding: 3f2a9c1b0d4e
      at: prices.py:12
      verdict: false alarm
      reason: Describes the new negative-price check, not the cache that's no longer cleared.

A finding is known by its fingerprint: a short hash of its place and text. At temperature
0 the model answers the same way every time, so a finding that comes back unchanged in a
later evaluation keeps its reading, and only new findings need reading. A finding whose
text changed in any way is a new finding.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, TypeAdapter, ValidationError

from pr_review_agent.domain import Finding, NonEmptyStr, SetupError

READINGS_FILE = "readings.yaml"  # next to the cases folder: eval/readings.yaml
FINGERPRINT_LENGTH = 12

Fingerprint = Annotated[str, StringConstraints(pattern=rf"^[0-9a-f]{{{FINGERPRINT_LENGTH}}}$")]


class Verdict(StrEnum):
    SAME_PROBLEM = "same problem"  # describes the planted problem it's near
    REAL_PROBLEM = "real problem"  # right, but not a planted problem it's near
    FALSE_ALARM = "false alarm"  # wrong, or describes no problem

    @property
    def is_right(self) -> bool:
        return self is not Verdict.FALSE_ALARM


class Reading(BaseModel):
    """A person's verdict on one finding, as written in the readings file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case: NonEmptyStr
    finding: Fingerprint
    at: str = ""  # where the finding was, only to help the person reading the file
    verdict: Verdict
    reason: NonEmptyStr


def fingerprint(finding: Finding) -> str:
    """A short hash of the finding's place and everything it says about the problem.

    The severity and status aren't part of it: the double-check can change those without
    changing what the finding claims, and the reading is about the claim.
    """
    parts = (
        finding.file,
        str(finding.line),
        finding.side,
        finding.explanation,
        finding.trigger or "",
        finding.current_behaviour or "",
        finding.expected_behaviour or "",
    )
    digest = hashlib.sha256("\n".join(parts).encode("utf-8"))
    return digest.hexdigest()[:FINGERPRINT_LENGTH]


@dataclass(frozen=True)
class Readings:
    """Every recorded reading, looked up by case and finding."""

    by_finding: dict[tuple[str, str], Reading] = field(default_factory=dict)

    def of(self, case: str, finding: Finding) -> Reading | None:
        return self.by_finding.get((case, fingerprint(finding)))

    def __len__(self) -> int:
        return len(self.by_finding)


NO_READINGS = Readings()
_READING_LIST = TypeAdapter(list[Reading])


def load_readings(path: Path) -> Readings:
    """The readings in `path`; none when the file doesn't exist yet."""
    if not path.is_file():
        return NO_READINGS
    fix = f"Correct {path}: a list of readings, each with case, finding, verdict and reason."
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        readings = _READING_LIST.validate_python(raw)
    except (yaml.YAMLError, ValidationError) as error:
        raise SetupError(f"The readings file {path} is invalid: {error}", fix) from None
    by_finding: dict[tuple[str, str], Reading] = {}
    for reading in readings:
        key = (reading.case, reading.finding)
        if key in by_finding:
            raise SetupError(
                f"The readings file {path} reads finding {reading.finding} of "
                f"{reading.case} twice.",
                "Keep one reading per finding.",
            )
        by_finding[key] = reading
    return Readings(by_finding)
