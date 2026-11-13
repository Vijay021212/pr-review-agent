"""Runs every case through the real pipeline, offline, as a dry run (FR-18, 7.1).

Only GitHub is replaced (by `FixtureGitHub`); the model, the stages, the rules and the
summary are the ones every review uses, so the scores measure the agent itself. Each
review writes its own run log, so any score can be traced back (FR-23).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from pr_review_agent.config import Settings
from pr_review_agent.domain import ChatModel, Deadline, ReviewIncomplete
from pr_review_agent.evaluation.cases import Case
from pr_review_agent.evaluation.fixture import FixtureGitHub
from pr_review_agent.evaluation.scoring import CaseRun
from pr_review_agent.factory import Components
from pr_review_agent.pipeline import run_review
from pr_review_agent.runlog import RunLog
from pr_review_agent.secrets import Masker

log = logging.getLogger("pr_review_agent")


def run_evaluation(
    cases: tuple[Case, ...],
    model: ChatModel,
    settings: Settings,
    *,
    runs: int,
    clock: Callable[[], float] = time.perf_counter,
) -> list[CaseRun]:
    """Review every case `runs` times. A setup problem stops everything; an incomplete
    review is recorded and the evaluation goes on (it counts as a miss)."""
    masker = Masker()
    logs = settings.state_dir / "eval"
    results = []
    for number in range(1, runs + 1):
        for index, case in enumerate(cases, start=1):
            log.info("Run %d of %d, case %d of %d: %s", number, runs, index, len(cases), case.name)
            components = Components(github=FixtureGitHub(case), model=model, masker=masker)
            ref = case.pull_request.ref
            started = clock()
            try:
                result = run_review(
                    ref,
                    components,
                    settings,
                    Deadline(settings.time_limit_seconds),
                    dry_run=True,
                    run_log=RunLog.create(logs, ref, masker),
                )
            except ReviewIncomplete as error:
                results.append(CaseRun(case, number, None, error.reason, clock() - started))
                continue
            results.append(CaseRun(case, number, result, None, clock() - started))
    return results
