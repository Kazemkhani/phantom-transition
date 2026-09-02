"""The phantom transition without voice, and the guard closing it.

These tests support one claim: the fault is a property of the interleaving, not
of a speech pipeline. There is no barge-in here, no audio, no turn detector and
no endpointing. There is a stop button, a tool call dispatched before the turn
ended, and real `asyncio` cancellation that arrives too late.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

from text_agent_stop_button import (
    StopButton,
    TRANSCRIPT,
    TextAgent,
    ToolCall,
    _cancel_and_wait,
    earned_transition_on_a_stopped_turn,
    legitimate_path,
    press_stop_at_token,
    unearned_transition,
)

from phantom_transition import Facts, Phase

EXAMPLE = pathlib.Path(__file__).resolve().parent.parent / "examples" / "text_agent_stop_button.py"


def run(coro):
    return asyncio.run(coro)


def refusals(agent):
    return [e.detail for e in agent.events if e.kind == "transition_refused"]


def kinds(agent):
    return [e.kind for e in agent.events]


# -- the mechanic ----------------------------------------------------------


def test_the_tool_call_executes_even_though_the_turn_was_stopped():
    agent = run(unearned_transition())
    assert "transition_committed" in kinds(agent)
    assert "reply_discarded" in kinds(agent)


def test_cancelling_a_finished_task_does_not_undo_its_effect():
    """The mechanism, on its own, in eleven lines of stdlib asyncio.

    Written the way the runtime writes it. `livekit-agents` 1.7.1 calls
    `await utils.aio.cancel_and_wait(exe_task)` at
    `voice/agent_activity.py:3611` and then commits the results of the tools
    that finished anyway (:3613-3629). This is why.
    """
    effects = []

    async def scenario():
        async def side_effect():
            effects.append("committed")

        task = asyncio.create_task(side_effect())
        await asyncio.sleep(0)  # the runtime yields; the task runs
        await _cancel_and_wait([task])  # the stop arrives, correctly handled
        return task

    task = run(scenario())
    assert effects == ["committed"]
    assert task.cancelled() is False


def test_the_partial_reply_never_reaches_the_screen():
    agent = run(unearned_transition())
    assert agent.screen == []  # the user saw nothing
    assert agent.phase is Phase.DISCOVERY  # the agent moved on anyway


def test_a_discarded_turn_records_no_fact():
    agent = run(unearned_transition())
    assert agent.facts == Facts()


# -- the reproduction ------------------------------------------------------


def test_unguarded_text_agent_reproduces_the_phantom_transition():
    """Scenario one, unguarded. No legitimate path reaches this state.

    The only turn that could have recorded `greeting_delivered` is the turn the
    user stopped, so DISCOVERY is a phase the facts never justified and never
    could have. Nothing in the transcript says so: the user's screen is empty
    and the log holds a committed transition and a discarded reply, unrelated.
    """
    agent = run(unearned_transition())
    assert agent.phase is Phase.DISCOVERY
    assert agent.facts.greeting_delivered is False
    assert refusals(agent) == []


def test_the_guard_refuses_the_unearned_transition():
    agent = run(unearned_transition(guard_enabled=True))
    assert agent.phase is Phase.GREETING
    assert refusals(agent) == ["entry conditions for DISCOVERY not met"]


# -- what each fix does and does not close ---------------------------------


def test_the_stop_check_alone_loses_the_race_it_is_trying_to_win():
    """`refuse_on_stop` is a race, and this is the run in which it loses.

    The stop is pressed one token into the reply, which is after the tool call
    was dispatched and after it ran. Checking `stop.pressed` inside the tool
    therefore sees `False` and the transition commits, exactly as the unguarded
    agent's does.
    """
    agent = run(unearned_transition(refuse_on_stop=True))
    assert agent.phase is Phase.DISCOVERY
    assert refusals(agent) == []


def test_the_stop_check_wins_the_race_when_the_stop_lands_first():
    agent = run(earned_transition_on_a_stopped_turn(refuse_on_stop=True))
    assert agent.phase is Phase.GREETING
    assert refusals(agent) == ["the turn was stopped"]


def test_the_facts_check_admits_a_transition_the_facts_already_justified():
    """Stated plainly, because the paper must not overclaim.

    In scenario two the greeting really was delivered on an earlier, completed
    turn. `PhaseGuard.check` is asked whether DISCOVERY may be entered, the
    evidence for DISCOVERY is on file, and the answer is yes. The transition
    commits on a stopped turn. The facts check bounds a phantom transition to
    one that was already earned; it does not, by itself, make the commit wait
    for the turn to finish.
    """
    agent = run(earned_transition_on_a_stopped_turn(guard_enabled=True))
    assert agent.phase is Phase.DISCOVERY
    assert refusals(agent) == []


def test_both_checks_together_close_both_scenarios():
    unearned = run(unearned_transition(guard_enabled=True, refuse_on_stop=True))
    earned = run(earned_transition_on_a_stopped_turn(guard_enabled=True, refuse_on_stop=True))
    assert unearned.phase is Phase.GREETING
    assert earned.phase is Phase.GREETING


def test_a_phantom_transition_cannot_cascade_under_the_guard():
    """The property that survives the race, and the reason it matters.

    A stopped turn writes no facts, so even where a transition slips through on
    a stopped turn, the phase it lands in cannot then be left: the evidence for
    the next gate was never recorded either.
    """
    agent = run(earned_transition_on_a_stopped_turn(guard_enabled=True))
    assert agent.phase is Phase.DISCOVERY
    assert agent.facts.discovery_answers == 0
    stop = StopButton().press()
    run(
        agent.handle_turn(
            "and then?",
            tool_calls=[ToolCall("advance_phase", {"target": Phase.PITCH})],
            reply=["Sure."],
            stop=stop,
        )
    )
    assert agent.phase is Phase.DISCOVERY
    assert refusals(agent) == ["entry conditions for PITCH not met"]


# -- the guard is a gate, not a wall ---------------------------------------


def test_the_guard_does_not_block_the_legitimate_path():
    for switches in ({}, {"guard_enabled": True}, {"guard_enabled": True, "refuse_on_stop": True}):
        agent = run(legitimate_path(**switches))
        assert agent.phase is Phase.CLOSE, switches
        assert refusals(agent) == [], switches


def test_a_stop_at_every_offset_in_the_reply_never_leaves_an_unearned_phase():
    """The whole space of stop offsets for this turn, not one chosen offset."""
    for offset in range(len(TRANSCRIPT)):
        agent = TextAgent(guard_enabled=True)
        stop = StopButton()
        run(
            agent.handle_turn(
                "hi",
                tool_calls=[ToolCall("advance_phase", {"target": Phase.DISCOVERY})],
                reply=TRANSCRIPT,
                stop=stop,
                on_token=press_stop_at_token(stop, offset),
            )
        )
        assert agent.phase is Phase.GREETING, offset
        assert agent.facts == Facts(), offset


def test_press_stop_at_token_fires_once_at_the_named_offset():
    stop = StopButton()
    hook = press_stop_at_token(stop, 2)
    hook(0, "a")
    hook(1, "b")
    assert stop.pressed is False
    hook(2, "c")
    assert stop.pressed is True


# -- the generality claim, checked rather than asserted --------------------


def test_the_reproduction_depends_on_nothing_but_the_standard_library():
    """The paper says the fault is not specific to voice. This is the check.

    If someone later reaches for a voice framework, an HTTP client or a model
    provider to make this example work, the claim it supports stops being true
    and this test fails.
    """
    allowed = {"__future__", "asyncio", "dataclasses", "typing", "phantom_transition"}
    tree = ast.parse(EXAMPLE.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[0])
    assert imported <= allowed, sorted(imported - allowed)
