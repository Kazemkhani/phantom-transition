# Verbatim copy of src/phantom_transition/interleaving.py from commit 97c0482
# on branch amir/science-v2 ("Reproduce the interleaving against asyncio, and
# discharge the reduction obligation"), vendored here so the judge-blindness
# corpus can source injection-turn state transitions from a real event loop
# without this branch depending on that one. When the branches merge, import
# from phantom_transition.interleaving instead and delete this file.
"""The interleaving, reproduced against a real concurrency runtime.

The models in `session.py` and `core.py` demonstrate the fault by writing the
ordering down: the transition is committed before the interruption is known.
That is honest about what it is, and a sceptical engineer is right to say it
proves only that the author can write two lines in that order.

This module removes that objection. It reproduces the same fault out of
`asyncio` primitives arranged the way a real-time voice pipeline arranges
them, with no framework, no network, no audio and no model:

* a speech task, standing in for text-to-speech playback of the reply;
* a generation task that decides on a tool call while the caller may still be
  speaking, which is what pre-emptive or speculative generation means;
* a tool task, spawned by the generation task, that performs a side effect;
* a voice-activity event that cancels the reply.

The property being demonstrated is a property of the runtime, not of the
author's line ordering: `Task.cancel()` cancels the task it is called on. It
does not cancel a task that task spawned. So cancelling the reply leaves the
tool task running, and its side effect lands after the turn is already known
to be invalid.

`asyncio.CancelledError` is delivered at the next suspension point, so a tool
that performs a synchronous mutation between two awaits cannot be interrupted
at all. That is the shape a phase-advance tool almost always has, and it is
why cancelling in-flight work is not a fix.

Everything here is deterministic. Ordering is imposed with events rather than
with sleeps, so the result does not depend on machine speed, and a run
recorded on one machine replays identically on another.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum


class Stage(Enum):
    """Where in the turn the barge-in lands, relative to the tool call."""

    BEFORE_TOOL_CALL_IS_ISSUED = "before the tool call is issued"
    AFTER_ISSUE_BEFORE_EFFECT = "after the call is issued, before its effect lands"
    AFTER_EFFECT = "after the effect has landed"


@dataclass
class Trace:
    """What happened, in the order it happened."""

    events: list[str] = field(default_factory=list)
    phase: str = "GREETING"
    reply_spoken: bool = False
    turn_invalidated: bool = False

    def record(self, event: str) -> None:
        self.events.append(event)

    @property
    def phantom(self) -> bool:
        """True when the phase moved on a turn whose reply was never delivered."""
        return self.turn_invalidated and self.phase != "GREETING" and not self.reply_spoken

    def __str__(self) -> str:
        return "\n".join(f"  {i + 1}. {e}" for i, e in enumerate(self.events))


async def _advance_phase(trace: Trace, target: str, effect_gate: asyncio.Event) -> str:
    """The transition tool.

    It awaits once before mutating, which is the friendliest possible case for
    cancellation: it gives the event loop a suspension point at which a
    CancelledError could be delivered. A real phase-advance tool that mutates
    session state synchronously has no such point and cannot be cancelled at
    all, so this understates the problem rather than overstating it.
    """
    trace.record(f"tool: advance_phase({target}) begins executing")
    await effect_gate.wait()
    trace.phase = target
    trace.record(f"tool: advance_phase({target}) has mutated the session state")
    return "ok"


async def _generate_reply(
    trace: Trace,
    target: str,
    issued: asyncio.Event,
    effect_gate: asyncio.Event,
    tool_tasks: list[asyncio.Task],
) -> None:
    """Speculative generation: decide and act before end of turn is known.

    The tool task is spawned rather than awaited inline, which is what a real
    pipeline does so that synthesis can start while the tool runs. It is also
    exactly what breaks cancellation: the spawned task has no structured link
    back to this one, so cancelling this task leaves it running.
    """
    trace.record("model: decides to advance the phase, on the turn in progress")
    task = asyncio.create_task(_advance_phase(trace, target, effect_gate))
    tool_tasks.append(task)
    issued.set()

    trace.record("tts: begins speaking the reply")
    try:
        await asyncio.sleep(3600)  # stands in for playback of the reply
        trace.reply_spoken = True
    except asyncio.CancelledError:
        trace.record("tts: playback cancelled, reply discarded")
        raise


async def run_turn(
    stage: Stage = Stage.AFTER_ISSUE_BEFORE_EFFECT,
    *,
    target: str = "DISCOVERY",
    cancel_tool_with_reply: bool = False,
) -> Trace:
    """Run one turn in which the caller barges in at `stage`.

    cancel_tool_with_reply models the obvious remedy: track the tool tasks and
    cancel them along with the reply. It closes the case where the tool is
    still suspended, and it does nothing for a tool that has already mutated,
    which is the common case for a synchronous state change.
    """
    trace = Trace()
    issued = asyncio.Event()
    effect_gate = asyncio.Event()
    tool_tasks: list[asyncio.Task] = []

    reply = asyncio.create_task(
        _generate_reply(trace, target, issued, effect_gate, tool_tasks)
    )

    if stage is not Stage.BEFORE_TOOL_CALL_IS_ISSUED:
        await issued.wait()
    # For the BEFORE case the reply task is deliberately never given a chance
    # to run. `create_task` schedules it; it does not start it. Yielding to the
    # loop here, even with `sleep(0)`, would let generation reach the tool call
    # and the case would no longer be the case it is named after.

    if stage is Stage.AFTER_EFFECT:
        effect_gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # The barge-in. This is everything the runtime does about it.
    trace.record("vad: caller speech detected, the turn is invalidated")
    trace.turn_invalidated = True
    reply.cancel()
    if cancel_tool_with_reply:
        for task in tool_tasks:
            task.cancel()
        trace.record("runtime: in-flight tool tasks cancelled with the reply")

    await asyncio.gather(reply, return_exceptions=True)
    trace.record("runtime: reply task is cancelled, turn marked interrupted")

    # The tool task was never awaited by the reply, so it is still scheduled.
    # Releasing the gate is the caller finishing their sentence, a downstream
    # service replying, a lock being released: ordinary progress that the
    # cancellation of the reply did nothing to prevent.
    effect_gate.set()
    results = await asyncio.gather(*tool_tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            trace.record("tool: was cancelled before it mutated anything")

    trace.record(f"final phase: {trace.phase}; reply delivered: {trace.reply_spoken}")
    return trace


def reproduce(
    stage: Stage = Stage.AFTER_ISSUE_BEFORE_EFFECT, **kwargs
) -> Trace:
    """Synchronous entry point, so a reader can call this from a notebook cell."""
    return asyncio.run(run_turn(stage, **kwargs))


def survey() -> dict[tuple[str, bool], bool]:
    """Every barge-in position, with and without cancelling the tool.

    Returns a map from (stage, cancel_tool_with_reply) to whether the turn
    ended in a phantom transition. The row that matters is the one where the
    tool is cancelled with the reply and the phase still moved.
    """
    out: dict[tuple[str, bool], bool] = {}
    for stage in Stage:
        for cancel in (False, True):
            out[(stage.value, cancel)] = reproduce(
                stage, cancel_tool_with_reply=cancel
            ).phantom
    return out


if __name__ == "__main__":  # pragma: no cover
    trace = reproduce()
    print(trace)
    print(f"\nphantom transition: {trace.phantom}\n")
    print(f"{'barge-in lands':50s} {'tool cancelled':>15s} {'phantom':>9s}")
    for (stage, cancel), phantom in survey().items():
        print(f"{stage:50s} {str(cancel):>15s} {str(phantom):>9s}")
