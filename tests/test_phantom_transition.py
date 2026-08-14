"""The fault, reproduced, and then closed by each of the three fixes."""

from phantom_transition import Phase, Session, ToolCall, Turn

ADVANCE_TO_DISCOVERY = ToolCall("advance_phase", {"target": Phase.DISCOVERY})


def _greeted(**kwargs) -> Session:
    """A session that has completed its greeting turn, so DISCOVERY is legal."""
    session = Session(**kwargs)
    session.handle_turn(Turn("hello"))
    return session


def test_phantom_transition_reproduces():
    """The bug.

    Pre-emptive generation on, no cancellation, no guard. The caller barges in,
    the speech is discarded, and yet the call is now in DISCOVERY. Nothing the
    caller did moved it there.
    """
    session = _greeted(
        preemptive_generation=True,
        cancel_handoff_on_interrupt=False,
        guard_enabled=False,
    )
    assert session.phase is Phase.GREETING

    events = session.handle_turn(Turn("wait, sorry", interrupted=True), [ADVANCE_TO_DISCOVERY])

    assert Phase.DISCOVERY == session.phase, "expected the fault to fire"
    kinds = [e.kind for e in events]
    assert "handoff_executed" in kinds
    assert "speech_discarded" in kinds
    assert "handoff_cancelled" not in kinds


def test_fix_one_cancel_handoff_after_execution():
    """Cancelling the handoff once the turn is known to be interrupted."""
    session = _greeted(
        preemptive_generation=True,
        cancel_handoff_on_interrupt=True,
        guard_enabled=False,
    )
    events = session.handle_turn(Turn("wait, sorry", interrupted=True), [ADVANCE_TO_DISCOVERY])

    assert session.phase is Phase.GREETING
    assert "handoff_cancelled" in [e.kind for e in events]


def test_fix_two_disable_preemptive_generation():
    """Not committing tool calls before end of speech closes it at source."""
    session = _greeted(
        preemptive_generation=False,
        cancel_handoff_on_interrupt=False,
        guard_enabled=False,
    )
    events = session.handle_turn(Turn("wait, sorry", interrupted=True), [ADVANCE_TO_DISCOVERY])

    assert session.phase is Phase.GREETING
    assert "handoff_executed" not in [e.kind for e in events]


def test_both_fixes_together_are_belt_and_braces():
    session = _greeted(preemptive_generation=True, cancel_handoff_on_interrupt=True)
    session.handle_turn(Turn("wait", interrupted=True), [ADVANCE_TO_DISCOVERY])
    assert session.phase is Phase.GREETING


def test_uninterrupted_turn_still_advances_normally():
    """The fix must not break the thing the design is for."""
    session = _greeted(preemptive_generation=True, cancel_handoff_on_interrupt=True)
    events = session.handle_turn(Turn("yes please"), [ADVANCE_TO_DISCOVERY])

    assert session.phase is Phase.DISCOVERY
    assert "handoff_executed" in [e.kind for e in events]
    assert "handoff_cancelled" not in [e.kind for e in events]
