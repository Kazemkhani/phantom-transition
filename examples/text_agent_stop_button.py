"""The same fault with no voice, no framework and no dependencies.

A text agent with a stop button. The user types, the agent streams a reply, and
the user can press stop halfway through. The half-written reply is wiped from
the screen, exactly as a chat interface does it.

The reply is not the only thing the turn produced. The model also emitted a
tool call, and the runtime dispatched it the moment it arrived rather than
waiting for the reply to finish, because that is how a tool's latency is hidden
behind the text the user is already reading. By the time the stop arrives, that
call has run. Cancelling its task does nothing: there is nothing left to cancel.

    t0  the model emits advance_phase(DISCOVERY) and starts streaming
    t1  the runtime dispatches the tool call
    t2  the user presses stop
    t3  the runtime discards the streamed reply and cancels the tool task
    t4  the tool task had already finished; the phase is DISCOVERY

Nothing in that sequence is specific to speech. There is no barge-in, no audio,
no turn detector, no endpointing and no interruption threshold. The ingredients
are a turn that can be invalidated after it has authorised a side effect, and a
runtime that dispatches the side effect before the turn is over. Voice makes it
constant. It does not make it.

The cancellation is real asyncio, and it is written the way the runtime writes
it: `_cancel_and_wait` below is the same shape as `livekit-agents` 1.7.1's
`await utils.aio.cancel_and_wait(exe_task)` at
`livekit/agents/voice/agent_activity.py:3611`, whose own comment two lines
later says the results of tools that finished are committed anyway.

Run it:

    python examples/text_agent_stop_button.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any, Callable, List, Optional, Sequence

from phantom_transition import Facts, Phase, PhaseGuard

__all__ = [
    "StopButton",
    "ToolCall",
    "TextAgent",
    "press_stop_at_token",
    "TRANSCRIPT",
]


class StopButton:
    """The user's stop control.

    One-way and observable, like `SpeechHandle.interrupted`
    (`livekit/agents/voice/speech_handle.py:108-110`). Pressing it twice is the
    same as pressing it once, and it is never un-pressed: a turn the user
    abandoned does not become valid again later.
    """

    def __init__(self) -> None:
        self._pressed = False

    @property
    def pressed(self) -> bool:
        return self._pressed

    def press(self) -> "StopButton":
        self._pressed = True
        return self


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    kind: str
    detail: str = ""


async def _cancel_and_wait(tasks: Sequence[asyncio.Task]) -> None:
    """Cancel every task and wait for it to settle.

    A task that has already finished ignores `cancel()` and keeps its result,
    which is the whole mechanism. This is deliberately the same shape as the
    runtime's own cancellation and not a weaker version of it: the point is
    that the correct, framework-blessed cancellation call does not undo a
    side effect that has already happened.
    """
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class TextAgent:
    """A phase-gated text agent, with the two fixes as switches.

    guard_enabled
        Check `PhaseGuard.check(current, target, facts)` at the tool boundary
        before writing the phase. The facts are written only by turns that
        completed, so a discarded turn leaves no evidence behind.

    refuse_on_stop
        Refuse a transition whose turn has already been stopped by the time the
        tool runs. This is a race and is documented as one: it closes the
        window only when the stop has landed first.

    The two are separate because they close different things, and the
    difference is the point of the example. Run
    `python examples/text_agent_stop_button.py` to see it.
    """

    def __init__(self, *, guard_enabled: bool = False, refuse_on_stop: bool = False) -> None:
        self.phase = Phase.GREETING
        self.facts = Facts()
        self.guard = PhaseGuard()
        self.guard_enabled = guard_enabled
        self.refuse_on_stop = refuse_on_stop
        self.events: List[Event] = []
        self.screen: List[str] = []

    async def handle_turn(
        self,
        user_message: str,
        *,
        tool_calls: Optional[Sequence[ToolCall]] = None,
        reply: Sequence[str] = (),
        stop: Optional[StopButton] = None,
        on_token: Optional[Callable[[int, str], Any]] = None,
        answers_a_discovery_question: bool = False,
    ) -> List[Event]:
        """One user turn, streamed, interruptible by `stop`."""
        stop = stop if stop is not None else StopButton()
        emitted: List[Event] = []
        streamed: List[str] = []

        # The model decided on this turn: a tool call, then a reply. The
        # runtime dispatches the call straight away rather than holding it
        # until the reply is done.
        tasks = [
            asyncio.create_task(self._execute(call, stop)) for call in (tool_calls or ())
        ]
        # The yield to the event loop that every runtime performs between
        # dispatching a call and awaiting its first output chunk. This is where
        # the dispatched call actually runs.
        await asyncio.sleep(0)

        for index, token in enumerate(reply):
            if stop.pressed:
                break
            streamed.append(token)
            if on_token is not None:
                on_token(index, token)
            await asyncio.sleep(0)

        for task in tasks:
            if task.done() and not task.cancelled():
                emitted.append(task.result())

        if stop.pressed:
            # The interface wipes the partial reply, and the runtime cancels
            # what the turn had in flight. Both are correct. Neither helps.
            emitted.append(Event("reply_discarded", "".join(streamed)))
            await _cancel_and_wait(tasks)
        else:
            self.screen.append("".join(streamed))
            emitted.append(Event("reply_delivered", "".join(streamed)))
            self._record(answers_a_discovery_question)

        self.events.extend(emitted)
        return emitted

    async def _execute(self, call: ToolCall, stop: StopButton) -> Event:
        if call.name != "advance_phase":
            return Event("tool_unknown", call.name)

        target = call.args.get("target")

        if self.refuse_on_stop and stop.pressed:
            return Event("transition_refused", "the turn was stopped")

        if self.guard_enabled:
            allowed, reason = self.guard.check(self.phase, target, self.facts)
            if not allowed:
                return Event("transition_refused", reason)

        if not isinstance(target, Phase):
            return Event("transition_refused", "unknown phase: " + repr(target))

        previous, self.phase = self.phase, target
        return Event("transition_committed", previous.name + "->" + target.name)

    def _record(self, answers_a_discovery_question: bool) -> None:
        """Write facts from what the turn actually did, never from what it said."""
        if self.phase is Phase.GREETING:
            self.facts = replace(self.facts, greeting_delivered=True)
        elif self.phase is Phase.DISCOVERY and answers_a_discovery_question:
            self.facts = replace(
                self.facts, discovery_answers=self.facts.discovery_answers + 1
            )
        elif self.phase is Phase.PITCH:
            self.facts = replace(self.facts, pitch_delivered=True)


def press_stop_at_token(stop: StopButton, index: int) -> Callable[[int, str], None]:
    """An `on_token` hook that presses stop once `index` tokens have streamed."""

    def hook(i: int, _token: str) -> None:
        if i == index:
            stop.press()

    return hook


TRANSCRIPT = ["Hello", " there,", " thanks", " for", " getting", " in", " touch."]


# -----------------------------------------------------------------------------
# The three scenarios
# -----------------------------------------------------------------------------


async def unearned_transition(**switches) -> TextAgent:
    """The stop lands on the first turn, before the greeting was ever delivered.

    No legitimate path reaches DISCOVERY here: the only turn that could have
    recorded `greeting_delivered` is the one the user threw away. This is the
    case the facts check closes outright, with no timing in it at all.
    """
    agent = TextAgent(**switches)
    stop = StopButton()
    await agent.handle_turn(
        "hi",
        tool_calls=[ToolCall("advance_phase", {"target": Phase.DISCOVERY})],
        reply=TRANSCRIPT,
        stop=stop,
        on_token=press_stop_at_token(stop, 1),
    )
    return agent


async def earned_transition_on_a_stopped_turn(**switches) -> TextAgent:
    """The greeting was delivered, and then a stop lands on the next turn.

    The facts do justify DISCOVERY here, so the facts check admits it. Only
    `refuse_on_stop` refuses it, and only because the stop happened to land
    before the tool ran. That is a race, and it is reported as one.
    """
    agent = TextAgent(**switches)
    await agent.handle_turn("hi", reply=TRANSCRIPT)
    stop = StopButton().press()
    await agent.handle_turn(
        "wait, sorry",
        tool_calls=[ToolCall("advance_phase", {"target": Phase.DISCOVERY})],
        reply=["Of", " course."],
        stop=stop,
    )
    return agent


async def legitimate_path(**switches) -> TextAgent:
    """Nothing is stopped, and the agent still gets all the way to CLOSE."""
    agent = TextAgent(**switches)
    await agent.handle_turn("hi", reply=TRANSCRIPT)
    await agent.handle_turn(
        "tell me more",
        tool_calls=[ToolCall("advance_phase", {"target": Phase.DISCOVERY})],
        reply=["What", " brought", " you", " here?"],
    )
    await agent.handle_turn("we need it by March", reply=["Noted."], answers_a_discovery_question=True)
    await agent.handle_turn("and the budget is agreed", reply=["Good."], answers_a_discovery_question=True)
    await agent.handle_turn(
        "so how does it work",
        tool_calls=[ToolCall("advance_phase", {"target": Phase.PITCH})],
        reply=["Here", " is", " how."],
    )
    await agent.handle_turn(
        "that sounds right",
        tool_calls=[ToolCall("advance_phase", {"target": Phase.CLOSE})],
        reply=["Shall", " we", " book", " a", " time?"],
    )
    return agent


async def _main() -> None:
    configurations = (
        ("unguarded", {}),
        ("refuse_on_stop only", {"refuse_on_stop": True}),
        ("guard only", {"guard_enabled": True}),
        ("both", {"guard_enabled": True, "refuse_on_stop": True}),
    )
    scenarios = (
        ("stop before the greeting was ever delivered", unearned_transition),
        ("stop after the greeting, on an earned transition", earned_transition_on_a_stopped_turn),
        ("nothing stopped", legitimate_path),
    )

    for title, scenario in scenarios:
        print("\n" + title)
        print("-" * len(title))
        for name, switches in configurations:
            agent = await scenario(**switches)
            reasons = [e.detail for e in agent.events if e.kind == "transition_refused"]
            print(
                "  {:<22} phase={:<9} {}".format(
                    name, agent.phase.name, ("refused: " + reasons[-1]) if reasons else ""
                )
            )

    print(
        "\nThe stop button cannot be pressed before the tool call it is meant to stop.\n"
        "refuse_on_stop wins the race in scenario two and loses it in scenario one,\n"
        "where the stop lands after the tool has already run. The facts check has no\n"
        "race in it: DISCOVERY needs a delivered greeting, and a discarded turn does\n"
        "not deliver one."
    )


if __name__ == "__main__":
    asyncio.run(_main())
