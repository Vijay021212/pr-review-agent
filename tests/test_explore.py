"""Tests for the explore stage, the agent loop (FR-5), with a scripted fake model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pr_review_agent.domain import (
    Deadline,
    ExploredContext,
    ExploreStop,
    ReviewScope,
    ToolAction,
)
from pr_review_agent.selection import EXPLORED_TOKENS
from pr_review_agent.stages import ModelCall
from pr_review_agent.stages.explore import ExploreStep, explore
from tests.builders import make_file, make_pull_request, make_understanding
from tests.fakes import EXPLORE_DONE, FakeChatModel, explore_step

CHANGED = make_file("src/stats.py", ["def average(numbers):", "    return None"])


step = explore_step


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    files = {
        "README.md": "# Stats\n",
        "src/stats.py": "def average(numbers):\n    return None\n",
        "src/report.py": "from stats import average\n\ndef show(xs):\n    return average(xs) * 2\n",
    }
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    return tmp_path


def run(
    model: FakeChatModel, root: Path, *, max_steps: int = 10, deadline: Deadline | None = None
) -> tuple[ExploredContext, tuple[ModelCall, ...]]:
    pull_request = make_pull_request(files=(CHANGED,))
    return explore(
        pull_request,
        ReviewScope(batches=((CHANGED,),)),
        make_understanding(),
        model,
        deadline or Deadline(1200),
        root,
        max_steps=max_steps,
    )


def test_done_at_once_runs_no_tool(repo: Path) -> None:
    explored, calls = run(FakeChatModel([EXPLORE_DONE]), repo)
    assert explored.stopped_by is ExploreStop.DONE
    assert [call.action for call in explored.tool_calls] == [ToolAction.DONE]
    assert explored.excerpts == ()
    assert explored.project_map.startswith("## Folder tree")
    assert [call.prompt for call in calls] == ["explore v2"]


def test_the_model_chooses_the_tools_step_by_step(repo: Path) -> None:
    model = FakeChatModel(
        [
            step("search", text="average("),
            step(
                "open_file",
                path="src/report.py",
                notes="src/report.py:4 multiplies the result of average(), so None crashes it.",
            ),
            step("done", notes="That's the only caller."),
        ]
    )
    explored, calls = run(model, repo)
    assert [(c.action, c.target, c.result) for c in explored.tool_calls] == [
        (ToolAction.SEARCH, "average(", "2 matches"),
        (ToolAction.OPEN_FILE, "src/report.py", "lines 1-4 of 4"),
        (ToolAction.DONE, "", "done"),
    ]
    assert explored.tool_calls[0].reason == "Because search."
    assert explored.stopped_by is ExploreStop.DONE
    assert explored.notes == (
        "src/report.py:4 multiplies the result of average(), so None crashes it.",
        "That's the only caller.",
    )
    (excerpt,) = explored.excerpts
    assert (excerpt.path, excerpt.start_line) == ("src/report.py", 1)
    assert "     4      return average(xs) * 2" in excerpt.text
    assert len(calls) == 3


def test_each_step_sees_the_last_result_and_the_steps_so_far(repo: Path) -> None:
    model = FakeChatModel(
        [step("search", text="average("), step("open_file", path="src/report.py"), EXPLORE_DONE]
    )
    run(model, repo)
    first, second, third = (call.user for call in model.calls)
    assert "<<<BEGIN PROJECT MAP>>>\n## Folder tree" in first
    assert "     2 +     return None" in first  # the most important diff
    assert "Steps so far" not in first
    assert "This is step 1 of at most 10." in first
    assert "1. search 'average(': 2 matches" in second
    assert "<<<BEGIN RESULT>>>\nsrc/report.py:4: return average(xs) * 2" in second
    assert "2. open_file 'src/report.py': lines 1-4 of 4" in third
    assert "src/report.py:4:" not in third.split("## Result of the last step")[1]


def test_a_refused_call_is_shown_to_the_model_and_the_loop_goes_on(repo: Path) -> None:
    model = FakeChatModel([step("open_file", path="../../secret.txt"), EXPLORE_DONE])
    explored, _ = run(model, repo)
    assert explored.tool_calls[0].result == "refused: '../../secret.txt' is outside the repository"
    assert "Refused: '../../secret.txt' is outside the repository" in model.calls[1].user
    assert explored.excerpts == ()


def test_the_step_limit_stops_the_loop(repo: Path) -> None:
    model = FakeChatModel([step("search", text="average(")] * 3)
    explored, calls = run(model, repo, max_steps=2)
    assert explored.stopped_by is ExploreStop.STEP_LIMIT
    assert len(explored.tool_calls) == 2
    assert len(calls) == 2
    assert "This is step 2 of at most 2." in model.calls[1].user


def test_its_share_of_the_time_limit_stops_the_loop(repo: Path) -> None:
    class SlowModel(FakeChatModel):
        """Each answer takes 400 seconds of the fake clock: 35% of 1200 s is 420 s."""

        def complete_json(self, *args: Any, **kwargs: Any) -> Any:
            clock[0] += 400
            return super().complete_json(*args, **kwargs)

    clock = [0.0]
    deadline = Deadline(1200, clock=lambda: clock[0])
    model = SlowModel([step("search", text="average(")] * 5)
    explored, calls = run(model, repo, deadline=deadline)
    assert explored.stopped_by is ExploreStop.TIME_LIMIT
    assert len(calls) == 2  # 0 s and 400 s were inside the share; 800 s is past it


def test_notes_are_kept_short(repo: Path) -> None:
    model = FakeChatModel([step("done", notes="x" * 1000)])
    explored, _ = run(model, repo)
    assert len(explored.notes[0]) < 500
    assert explored.notes[0].endswith("more characters left out]")


def test_excerpts_stay_within_the_room_for_explored_code(tmp_path: Path) -> None:
    for number in range(12):
        lines = [f"line_{number}_{n} = {'y' * 60}" for n in range(100)]
        (tmp_path / f"m{number}.py").write_text("\n".join(lines), encoding="utf-8")
    opens = [step("open_file", path=f"m{number}.py") for number in range(12)]
    explored, _ = run(FakeChatModel([*opens, EXPLORE_DONE]), tmp_path, max_steps=13)
    total = sum(len(excerpt.text) for excerpt in explored.excerpts)
    assert 0 < total <= EXPLORED_TOKENS * 3
    assert all(len(excerpt.text.splitlines()) == 60 for excerpt in explored.excerpts)
    # The newest files are the ones kept, in the order they were opened.
    assert explored.excerpts[-1].path == "m11.py"
    paths = [excerpt.path for excerpt in explored.excerpts]
    assert paths == sorted(paths, key=lambda p: int(p[1:-3]))


def test_an_opened_file_from_a_later_line_keeps_that_line(repo: Path) -> None:
    model = FakeChatModel([step("open_file", path="./src/report.py", start_line=3), EXPLORE_DONE])
    explored, _ = run(model, repo)
    assert (explored.excerpts[0].path, explored.excerpts[0].start_line) == ("src/report.py", 3)


def test_a_review_without_reviewed_files_still_explores(repo: Path) -> None:
    model = FakeChatModel([EXPLORE_DONE])
    explore(
        make_pull_request(files=()),
        ReviewScope(),
        make_understanding(),
        model,
        Deadline(60),
        repo,
        max_steps=3,
    )
    assert "Diff of the most important files" not in model.calls[0].user


def test_a_repeated_step_isnt_run_again_and_the_model_is_told(repo: Path) -> None:
    search = step("search", text="average(")
    model = FakeChatModel([search, step("search", text=" average( "), EXPLORE_DONE])
    explored, _ = run(model, repo)
    assert [c.result for c in explored.tool_calls] == [
        "2 matches",
        "repeated, not run again",
        "done",
    ]
    assert "You already took exactly this step" in model.calls[2].user
    assert explored.stopped_by is ExploreStop.DONE


def test_a_second_repeat_ends_exploring(repo: Path) -> None:
    opening = step("open_file", path="src/report.py")
    model = FakeChatModel(
        [opening, step("open_file", path="./src/report.py"), opening, EXPLORE_DONE]
    )
    explored, calls = run(model, repo)
    assert explored.stopped_by is ExploreStop.REPEATING
    assert len(calls) == 3
    assert len(explored.excerpts) == 1  # the file was read once


def test_the_same_file_from_another_line_isnt_a_repeat(repo: Path) -> None:
    model = FakeChatModel(
        [
            step("open_file", path="src/report.py"),
            step("open_file", path="src/report.py", start_line=3),
            EXPLORE_DONE,
        ]
    )
    explored, _ = run(model, repo)
    assert [c.result for c in explored.tool_calls][:2] == ["lines 1-4 of 4", "lines 3-4 of 4"]


def test_every_field_of_a_step_is_required() -> None:
    schema = json.loads(json.dumps(ExploreStep.model_json_schema()))
    assert set(schema["required"]) == {"action", "path", "text", "start_line", "reason", "notes"}
