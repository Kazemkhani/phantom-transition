"""Tests for the asyncio reproduction.

These assert the ordering the fault consists of, against a real event loop
rather than against two lines the author wrote in a chosen order. The claim
under test is a property of task cancellation: cancelling a task does not
cancel the tasks it spawned, so a side effect authorised by a turn can land
after that turn has been invalidated.
"""

from __future__ import annotations

import asyncio

import pytest

from phantom_transition.interleaving import Stage, Trace, reproduce, run_turn, survey


def index_of(trace: Trace, fragment: str) -> int:
    for i, event in enumerate(trace.events):
        if fragment in event:
            return i
    raise AssertionError(f"no event containing {fragment!r} in:\n{trace}")


def test_the_effect_lands_after_the_turn_is_known_to_be_invalid():
    """The fault, stated as an ordering over real event-loop scheduling."""
    trace = reproduce(Stage.AFTER_ISSUE_BEFORE_EFFECT)

    invalidated = index_of(trace, "the turn is invalidated")
    cancelled = index_of(trace, "reply task is cancelled")
    mutated = index_of(trace, "has mutated the session state")

    assert invalidated < cancelled < mutated
    assert trace.phase == "DISCOVERY"
    assert trace.reply_spoken is False
    assert trace.phantom is True


def test_cancelling_the_reply_does_not_cancel_the_tool_it_spawned():
    """The mechanism, isolated. This is why the ordering above is possible."""
    trace = reproduce(Stage.AFTER_ISSUE_BEFORE_EFFECT)
    assert "was cancelled before it mutated anything" not in "\n".join(trace.events)
    assert trace.phase == "DISCOVERY"


def test_cancelling_the_tool_too_closes_the_window_only_before_the_effect():
    """The obvious remedy, and the exact size of what it buys.

    Tracking in-flight tool tasks and cancelling them with the reply is what
    upstream cancellation fixes and per-tool interruption opt-outs amount to.
    It works while the tool is still suspended. It does nothing once the tool
    has mutated, and a synchronous state change between two awaits is never
    suspended at all.
    """
    before_effect = reproduce(Stage.AFTER_ISSUE_BEFORE_EFFECT, cancel_tool_with_reply=True)
    assert before_effect.phantom is False
    assert before_effect.phase == "GREETING"

    after_effect = reproduce(Stage.AFTER_EFFECT, cancel_tool_with_reply=True)
    assert after_effect.phantom is True, "cancellation cannot undo a mutation that already ran"
    assert after_effect.phase == "DISCOVERY"


def test_a_barge_in_before_the_decision_is_harmless():
    """The turn never authorised anything, so there is nothing to survive it."""
    for cancel in (False, True):
        trace = reproduce(Stage.BEFORE_TOOL_CALL_IS_ISSUED, cancel_tool_with_reply=cancel)
        assert trace.phantom is False
        assert trace.phase == "GREETING"


def test_the_survey_matches_the_reported_table():
    """Every cell of the table the paper prints, checked."""
    assert survey() == {
        ("before the tool call is issued", False): False,
        ("before the tool call is issued", True): False,
        ("after the call is issued, before its effect lands", False): True,
        ("after the call is issued, before its effect lands", True): False,
        ("after the effect has landed", False): True,
        ("after the effect has landed", True): True,
    }


def test_the_reproduction_is_deterministic():
    """A failure that cannot be replayed is not evidence. This one replays.

    Ordering is imposed with events rather than sleeps, so the trace does not
    depend on machine speed and a run recorded here replays elsewhere.
    """
    baseline = tuple(reproduce(Stage.AFTER_ISSUE_BEFORE_EFFECT).events)
    for _ in range(100):
        assert tuple(reproduce(Stage.AFTER_ISSUE_BEFORE_EFFECT).events) == baseline
    for _ in range(20):
        assert survey() == survey()


def test_no_sleep_based_timing_is_used_to_order_events():
    """Guard against the reproduction quietly becoming timing-dependent.

    The only sleep in the module stands in for playback and is unbounded, so
    it is cancelled rather than waited out. A short sleep introduced to make
    an ordering come out right would make this test fail.
    """
    import inspect

    from phantom_transition import interleaving

    source = inspect.getsource(interleaving)
    sleeps = [
        line.strip()
        for line in source.splitlines()
        if "asyncio.sleep(" in line and not line.strip().startswith("#")
    ]
    for line in sleeps:
        assert "sleep(3600)" in line or "sleep(0)" in line, f"timing-dependent sleep: {line}"


@pytest.mark.parametrize("stage", list(Stage))
def test_every_run_terminates_and_leaves_no_pending_tasks(stage):
    """A reproduction that leaks a task would report a phase nobody committed."""

    async def check() -> int:
        await run_turn(stage)
        return len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

    assert asyncio.run(check()) == 0


def test_the_trace_records_the_reply_was_never_delivered():
    """The transcript looks healthy: the caller heard nothing unusual."""
    trace = reproduce(Stage.AFTER_ISSUE_BEFORE_EFFECT)
    assert trace.reply_spoken is False
    assert trace.turn_invalidated is True
    assert "playback cancelled, reply discarded" in "\n".join(trace.events)
