"""Retries for temporary problems with GitHub or a model provider (NFR-10).

A rate limit, a server error or a network blip is retried up to 4 times, waiting 1, 2, 4
and 8 seconds (or as long as the service asks with `Retry-After`). If it still fails, the
review stops and is reported as incomplete (NFR-4). No wait ever runs past the hard time
limit: when the next wait wouldn't fit, the review stops straight away.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from pr_review_agent.domain import Deadline, ReviewIncomplete

RETRIES = 4
FIRST_WAIT_SECONDS = 1.0
TEMPORARY_STATUSES = frozenset({429, 500, 502, 503, 504})


def is_temporary(response: httpx.Response) -> bool:
    return response.status_code in TEMPORARY_STATUSES


def retry_after(response: httpx.Response) -> float | None:
    """The wait the service asked for, in seconds, if it gave one as a number."""
    value = response.headers.get("retry-after", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def send_with_retries(
    send: Callable[[], httpx.Response],
    *,
    service: str,
    deadline: Deadline,
    temporary: Callable[[httpx.Response], bool] = is_temporary,
    wait_for: Callable[[httpx.Response], float | None] = retry_after,
    sleep: Callable[[float], None] = time.sleep,
    retry_network_errors: bool = True,
) -> httpx.Response:
    """Call `send` until it gives a lasting answer, and return that response.

    A lasting answer is any response that isn't temporary, including errors like 404:
    those are for the caller to explain. `httpx.ConnectError` is passed straight to the
    caller, because "nothing is listening" usually means a setup problem (Ollama not
    started), not a blip; other network errors and timeouts are retried.

    A write that may already have happened (posting a review) passes
    `retry_network_errors=False`: when the connection drops, nobody knows whether GitHub
    received it, so sending it again could post it twice. The review stops and says so.
    """
    attempt = 0
    while True:
        deadline.check(f"{service} answered")
        wait = FIRST_WAIT_SECONDS * 2**attempt
        try:
            response = send()
        except httpx.ConnectError:
            raise
        except httpx.TransportError as error:  # timeouts and dropped connections
            problem = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
            if not retry_network_errors:
                raise ReviewIncomplete(
                    f"the connection to {service} failed while sending ({problem}); it may or "
                    "may not have arrived, so it wasn't sent again"
                ) from error
        else:
            if not temporary(response):
                return response
            problem = f"status {response.status_code}"
            wait = max(wait, wait_for(response) or 0.0)
            response.close()  # frees the connection of a streamed download before retrying
        if attempt == RETRIES:
            raise ReviewIncomplete(
                f"{service} kept failing after {RETRIES + 1} attempts ({problem})"
            )
        if wait >= deadline.remaining():
            deadline.check(f"{service} answered")  # raises if the time is already up
            raise ReviewIncomplete(
                f"{service} had a temporary problem ({problem}), and waiting {wait:g} s "
                "to try again would pass the time limit"
            )
        sleep(wait)
        attempt += 1
