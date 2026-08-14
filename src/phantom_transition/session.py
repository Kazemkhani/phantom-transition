"""A minimal phase-gated multi-agent conversation, and the fault it creates.

The system under study is a voice agent split into four specialised agents, one
per phase of a call. Each agent hands off to the next by issuing a tool call.
This is a common and sensible design: each agent gets a smaller, sharper brief.

It has a failure mode that nobody designs for.

A real-time voice agent generates its next turn while the caller is still
speaking, because waiting for end-of-speech costs latency the caller can hear.
When the caller barges in, the speech and reasoning already in flight are
discarded. But a tool call issued a moment earlier has already executed. If that
tool call was a phase handoff, the conversation has silently advanced to a stage
the caller never triggered, and nothing in the transcript says so.

This module reproduces that, and then fixes it three ways.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Phase(IntEnum):
    """Call phases, in the only order they may legally be traversed."""

    GREETING = 0
    DISCOVERY = 1
    PITCH = 2
    CLOSE = 3


@dataclass(frozen=True)
class Turn:
    """One caller turn.

    interrupted marks a barge-in: the caller began speaking again before the
    agent finished its reply, so everything the agent had in flight is dropped.
    """

    utterance: str
    interrupted: bool = False
    answers_a_discovery_question: bool = False


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    kind: str
    detail: str = ""


@dataclass
class Facts:
    """Structured state the guard reasons over.

    Nothing here is set by the model or by anything the caller says. Each field
    is written by the runtime from an observed event. This is the whole point:
    the guard's inputs are not reachable from the conversation.
    """

    greeting_delivered: bool = False
    discovery_answers: int = 0
    pitch_delivered: bool = False


ENTRY_CONDITIONS = {
    Phase.DISCOVERY: lambda f: f.greeting_delivered,
    Phase.PITCH: lambda f: f.discovery_answers >= 2,
    Phase.CLOSE: lambda f: f.pitch_delivered,
}


class PhaseGuard:
    """Tool-layer guard on phase progression.

    The guard runs at the tool boundary, not in the prompt. A model cannot be
    instructed past it and a caller cannot phrase their way through it, because
    it never reads the utterance.
    """

    def check(self, current: Phase, target: Any, facts: Facts) -> tuple[bool, str]:
        if not isinstance(target, Phase):
            return False, f"unknown phase: {target!r}"
        if target == current:
            return False, "already in that phase"
        if target < current:
            return False, "phase progression is forward only"
        if target != current + 1:
            return False, f"cannot skip from {current.name} to {target.name}"
        condition = ENTRY_CONDITIONS.get(target)
        if condition is not None and not condition(facts):
            return False, f"entry conditions for {target.name} not met"
        return True, "ok"


class Session:
    """A call.

    Three switches, so the fault and each fix can be isolated:

    preemptive_generation
        The agent commits its tool calls before the caller has finished
        speaking. True reproduces the production default that causes the fault.

    cancel_handoff_on_interrupt
        After the turn resolves, undo any phase handoff that executed on a turn
        that turned out to be interrupted.

    guard_enabled
        Enforce phase progression at the tool layer rather than by prompt.
    """

    def __init__(
        self,
        *,
        preemptive_generation: bool = False,
        cancel_handoff_on_interrupt: bool = True,
        guard_enabled: bool = True,
    ) -> None:
        self.phase = Phase.GREETING
        self.facts = Facts()
        self.preemptive_generation = preemptive_generation
        self.cancel_handoff_on_interrupt = cancel_handoff_on_interrupt
        self.guard_enabled = guard_enabled
        self.guard = PhaseGuard()
        self.events: list[Event] = []

    # -- internals ---------------------------------------------------------
    def _execute(self, call: ToolCall) -> Event:
        if call.name != "advance_phase":
            return Event("tool_unknown", call.name)

        target = call.args.get("target")
        if self.guard_enabled:
            allowed, reason = self.guard.check(self.phase, target, self.facts)
            if not allowed:
                return Event("handoff_denied", reason)

        if not isinstance(target, Phase):
            return Event("handoff_denied", f"unknown phase: {target!r}")

        previous, self.phase = self.phase, target
        return Event("handoff_executed", f"{previous.name}->{target.name}")

    def _rollback(self, event: Event) -> Event:
        previous_name = event.detail.split("->")[0]
        self.phase = Phase[previous_name]
        return Event("handoff_cancelled", event.detail)

    # -- public API --------------------------------------------------------
    def handle_turn(self, turn: Turn, tool_calls: list[ToolCall] | None = None) -> list[Event]:
        """Process one caller turn and return the events it produced."""
        emitted: list[Event] = []
        tool_calls = tool_calls or []

        # A turn known to be interrupted only reaches the tool layer at all
        # when the agent generated pre-emptively. Otherwise the runtime has
        # already seen the barge-in and never commits the call.
        commits_tools = self.preemptive_generation or not turn.interrupted

        if commits_tools:
            for call in tool_calls:
                emitted.append(self._execute(call))

        if turn.interrupted:
            emitted.append(Event("speech_discarded"))
            if self.cancel_handoff_on_interrupt:
                for event in list(emitted):
                    if event.kind == "handoff_executed":
                        emitted.append(self._rollback(event))
        else:
            emitted.append(Event("spoke"))
            self._record(turn)

        self.events.extend(emitted)
        return emitted

    def _record(self, turn: Turn) -> None:
        """Write structured facts from observed events, never from utterances."""
        if self.phase is Phase.GREETING:
            self.facts.greeting_delivered = True
        elif self.phase is Phase.DISCOVERY and turn.answers_a_discovery_question:
            self.facts.discovery_answers += 1
        elif self.phase is Phase.PITCH:
            self.facts.pitch_delivered = True


__all__ = ["Phase", "Turn", "ToolCall", "Event", "Facts", "PhaseGuard", "Session"]
