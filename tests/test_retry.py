"""Tests for retrying temporary problems (NFR-10)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from pr_review_agent.domain import Deadline, ReviewIncomplete, TimeLimitReached
from pr_review_agent.retry import is_temporary, retry_after, send_with_retries


class Script:
    """Gives the scripted outcomes in order: a response, or an exception to raise."""

    def __init__(self, *outcomes: httpx.Response | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self) -> httpx.Response:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run(
    script: Script,
    *,
    seconds: float = 600,
    temporary: Callable[[httpx.Response], bool] = is_temporary,
    retry_network_errors: bool = True,
) -> tuple[httpx.Response, list[float]]:
    waits: list[float] = []
    response = send_with_retries(
        script,
        service="GitHub",
        deadline=Deadline(seconds),
        sleep=waits.append,
        temporary=temporary,
        retry_network_errors=retry_network_errors,
    )
    return response, waits


def test_a_write_isnt_sent_again_after_a_dropped_connection() -> None:
    script = Script(httpx.ReadTimeout("no answer"), httpx.Response(200))
    with pytest.raises(ReviewIncomplete, match="it may or may not have arrived"):
        run(script, retry_network_errors=False)
    assert script.calls == 1


def test_a_lasting_answer_is_returned_at_once() -> None:
    response, waits = run(Script(httpx.Response(200)))
    assert response.status_code == 200
    assert waits == []


def test_errors_that_wont_go_away_are_returned_for_the_caller_to_explain() -> None:
    response, waits = run(Script(httpx.Response(404)))
    assert response.status_code == 404
    assert waits == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_temporary_statuses_are_retried(status: int) -> None:
    response, waits = run(Script(httpx.Response(status), httpx.Response(200)))
    assert response.status_code == 200
    assert waits == [1.0]


def test_network_blips_and_timeouts_are_retried() -> None:
    script = Script(httpx.ReadTimeout("slow"), httpx.RemoteProtocolError(""), httpx.Response(200))
    response, waits = run(script)
    assert response.status_code == 200
    assert waits == [1.0, 2.0]


def test_the_wait_grows_and_then_the_review_is_incomplete() -> None:
    script = Script(*[httpx.Response(503) for _ in range(5)])
    with pytest.raises(
        ReviewIncomplete, match=r"GitHub kept failing after 5 attempts \(status 503\)"
    ):
        run(script)
    assert script.calls == 5


def test_waits_are_1_2_4_and_8_seconds() -> None:
    waits: list[float] = []
    script = Script(*[httpx.Response(500) for _ in range(5)])
    with pytest.raises(ReviewIncomplete):
        send_with_retries(script, service="X", deadline=Deadline(600), sleep=waits.append)
    assert waits == [1.0, 2.0, 4.0, 8.0]


def test_retry_after_is_honoured() -> None:
    limited = httpx.Response(429, headers={"Retry-After": "30"})
    _, waits = run(Script(limited, httpx.Response(200)))
    assert waits == [30.0]


def test_a_connection_refused_goes_straight_to_the_caller() -> None:
    script = Script(httpx.ConnectError("refused"))
    with pytest.raises(httpx.ConnectError):
        run(script)
    assert script.calls == 1


def test_no_wait_runs_past_the_time_limit() -> None:
    limited = httpx.Response(429, headers={"Retry-After": "120"})
    with pytest.raises(ReviewIncomplete, match="would pass the time limit") as caught:
        run(Script(limited), seconds=60)
    assert not isinstance(caught.value, TimeLimitReached)
    assert "status 429" in str(caught.value)


def test_nothing_is_sent_once_the_time_is_up() -> None:
    clock = iter([0.0, 100.0])
    deadline = Deadline(10, clock=lambda: next(clock))
    script = Script(httpx.Response(200))
    with pytest.raises(TimeLimitReached, match="before GitHub answered"):
        send_with_retries(script, service="GitHub", deadline=deadline)
    assert script.calls == 0


def test_the_caller_can_say_what_counts_as_temporary() -> None:
    def rate_limited(response: httpx.Response) -> bool:
        return response.status_code == 403

    response, waits = run(Script(httpx.Response(403), httpx.Response(200)), temporary=rate_limited)
    assert response.status_code == 200
    assert waits == [1.0]


@pytest.mark.parametrize(
    ("header", "seconds"),
    [("5", 5.0), ("0.5", 0.5), ("-3", 0.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("", None)],
)
def test_retry_after_reads_seconds_only(header: str, seconds: float | None) -> None:
    assert retry_after(httpx.Response(429, headers={"Retry-After": header})) == seconds
