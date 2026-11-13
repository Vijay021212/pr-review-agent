"""Runs the review stages in a fixed order (design section 2, ADR-002).

    fetch -> select files -> understand -> snapshot -> explore -> find (passes A, B)
    -> double-check -> recommend

**Failure rule (NFR-4):** if any stage fails, or the hard time limit is reached, the
pipeline stops at once and raises `ReviewIncomplete`: nothing more runs, no recommendation
is given, and a broken review can never look like a clean one. A setup problem (a bad
token, a closed pull request) is raised as `SetupError` instead, so it can say how to fix it.

Progress goes to the `pr_review_agent` logger; the command line shows it, and code using
the package can show or ignore it like any other Python logging.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from pr_review_agent.config import Settings
from pr_review_agent.domain import (
    CheckedFindings,
    Deadline,
    DroppedFinding,
    ExploredContext,
    Finding,
    PullRequestRef,
    ReviewIncomplete,
    ReviewResult,
    SetupError,
    StageTiming,
)
from pr_review_agent.factory import Components
from pr_review_agent.github.snapshot import snapshot
from pr_review_agent.recommendation import recommend
from pr_review_agent.runlog import RunLog
from pr_review_agent.selection import select_files
from pr_review_agent.sensitive import find_sensitive
from pr_review_agent.stages import ModelCall
from pr_review_agent.stages.explore import explore
from pr_review_agent.stages.fetch import fetch
from pr_review_agent.stages.find import PASSES, find_problems
from pr_review_agent.stages.understand import understand
from pr_review_agent.stages.verify import verify
from pr_review_agent.version import __version__

log = logging.getLogger("pr_review_agent")


class _Stages:
    """Times and logs each stage, and turns any unexpected error into an incomplete review."""

    def __init__(self, run_log: RunLog) -> None:
        self.timings: list[StageTiming] = []
        self._run_log = run_log

    @contextmanager
    def run(self, name: str, doing: str) -> Iterator[None]:
        log.info("%s...", doing)
        self._run_log.record("stage started", stage=name)
        started = time.perf_counter()
        try:
            yield
        except (SetupError, ReviewIncomplete) as error:
            self._failed(name, started, str(error))
            raise
        except Exception as error:
            self._failed(name, started, f"{type(error).__name__}: {error}")
            raise ReviewIncomplete(
                f"the {name} stage failed ({type(error).__name__}: {error})"
            ) from error
        seconds = time.perf_counter() - started
        self.timings.append(StageTiming(stage=name, seconds=seconds))
        self._run_log.record("stage finished", stage=name, seconds=round(seconds, 3))

    def _failed(self, name: str, started: float, error: str) -> None:
        seconds = time.perf_counter() - started
        self.timings.append(StageTiming(stage=name, seconds=seconds))
        self._run_log.record("stage failed", stage=name, seconds=round(seconds, 3), error=error)


def run_review(
    ref: PullRequestRef,
    components: Components,
    settings: Settings,
    deadline: Deadline,
    *,
    dry_run: bool,
    run_log: RunLog,
) -> ReviewResult:
    """Run the whole review, writing every step to `run_log` (FR-23)."""
    run_log.record(
        "review started",
        pull_request=str(ref),
        dry_run=dry_run,
        version=__version__,
        provider=components.model.provider,
        model=components.model.model,
        # Every setting but the secrets, which are never written anywhere (NFR-5).
        settings=settings.model_dump(mode="json", exclude={"github_token", "api_key"}),
    )
    try:
        result = _run_stages(ref, components, settings, deadline, dry_run, run_log)
    except SetupError as error:
        run_log.record("review stopped by a setup problem", problem=error.problem, fix=error.fix)
        raise
    except ReviewIncomplete as error:
        run_log.record("review incomplete", reason=error.reason)
        raise
    run_log.record(
        "review complete",
        recommendation=result.recommendation.value,
        rule=result.recommendation.rule,
        reasons=list(result.recommendation.reasons),
        seconds=round(result.seconds, 3),
    )
    return result


def _run_stages(
    ref: PullRequestRef,
    components: Components,
    settings: Settings,
    deadline: Deadline,
    dry_run: bool,
    run_log: RunLog,
) -> ReviewResult:
    stages = _Stages(run_log)
    calls: list[ModelCall] = []
    model = components.model

    with stages.run("fetch", f"Reading pull request {ref}"):
        pull_request = fetch(components.github, ref, deadline)
        run_log.record(
            "pull request read", head_sha=pull_request.head_sha, files=len(pull_request.files)
        )
    with stages.run("select files", "Choosing the files to review"):
        scope = select_files(pull_request, context_tokens=settings.context_tokens)
        run_log.record(
            "files selected",
            batches=[[file.path for file in batch] for batch in scope.batches],
            skipped=[{"path": skip.path, "reason": skip.reason} for skip in scope.skipped],
        )
    with stages.run("understand", "Working out what the pull request is for"):
        understanding, call = understand(pull_request, scope, model, deadline)
        _record_calls(run_log, "understand", [call])
        calls.append(call)
    # The snapshot stays until the review ends, and is removed even when a stage fails.
    with ExitStack() as cleanup:
        with stages.run("snapshot", "Downloading the repository at the reviewed commit"):
            root = cleanup.enter_context(snapshot(components.github, pull_request, deadline))
        with stages.run("explore", "Exploring related code"):
            explored, explore_calls = explore(
                pull_request,
                scope,
                understanding,
                model,
                deadline,
                root,
                max_steps=settings.explore_max_steps,
            )
            _record_calls(run_log, "explore", explore_calls)
            _record_exploring(run_log, explored)
            calls += explore_calls
        kept: list[Finding] = []
        dropped: list[DroppedFinding] = []
        with stages.run("find", f"Looking for problems in {len(scope.batches)} batch(es) of files"):
            for finding_pass in PASSES:
                log.info("Pass: %s...", finding_pass.focus)
                found = find_problems(
                    finding_pass, pull_request, scope, understanding, explored, model, deadline
                )
                _record_calls(run_log, "find", found.calls)
                calls += found.calls
                kept += found.findings
                dropped += found.dropped
        with stages.run("double-check", f"Double-checking {len(kept)} finding(s)"):
            findings, verify_calls = verify(
                tuple(kept), tuple(dropped), pull_request.files, root, model, deadline
            )
            _record_calls(run_log, "double-check", verify_calls)
            calls += verify_calls
        _record_findings(run_log, findings)
        with stages.run("recommend", "Applying the recommendation rules"):
            changed_paths = [file.path for file in pull_request.files]
            recommendation = recommend(
                findings, scope, understanding, find_sensitive(changed_paths)
            )

    result = ReviewResult(
        pull_request=pull_request,
        scope=scope,
        understanding=understanding,
        explored=explored,
        findings=findings,
        recommendation=recommendation,
        provider=model.provider,
        model=model.model,
        prompt_versions=tuple(dict.fromkeys(call.prompt for call in calls)),
        timings=tuple(stages.timings),
        dry_run=dry_run,
    )
    # The result leaves the program (to the terminal, or to code using the package), so
    # every text in it is masked, including anything the model quoted (FR-21).
    return ReviewResult.model_validate(components.masker.mask_data(result.model_dump(mode="json")))


def _record_calls(
    run_log: RunLog, stage: str, calls: list[ModelCall] | tuple[ModelCall, ...]
) -> None:
    for call in calls:
        run_log.record(
            "model call",
            stage=stage,
            prompt=call.prompt,
            attempts=len(call.replies),
            retry_reason=call.retry_reason,
            replies=[
                {
                    "model": reply.model,
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                    "seconds": round(reply.seconds, 3),
                }
                for reply in call.replies
            ],
        )


def _record_exploring(run_log: RunLog, explored: ExploredContext) -> None:
    """Every tool call the model made, and why exploring stopped (FR-23)."""
    for call in explored.tool_calls:
        run_log.record(
            "tool call",
            action=call.action,
            target=call.target,
            reason=call.reason,
            result=call.result,
        )
    run_log.record(
        "exploring stopped",
        stopped_by=explored.stopped_by,
        notes=list(explored.notes),
        excerpts=[f"{e.path}:{e.start_line}" for e in explored.excerpts],
    )


def _record_findings(run_log: RunLog, findings: CheckedFindings) -> None:
    for finding in findings.kept:
        run_log.record("finding kept", **_where(finding))
    for dropped in findings.dropped:
        run_log.record("finding dropped", **_where(dropped.finding), reason=dropped.reason)


def _where(finding: Finding) -> dict[str, object]:
    return {
        "file": finding.file,
        "line": finding.line,
        "category": finding.category,
        "level": int(finding.level),
        "severity": finding.severity,
        "status": finding.status,
    }
