"""Adopt the phase guard inside a LiveKit Agents session.

The core package knows nothing about any runtime. This module is the adapter
that puts the same guard on the tool boundary of a `livekit-agents` 1.7.x
`AgentSession`, in five lines of a user's own code.

Nothing here imports `livekit.agents` at module scope. Every import is inside a
function, so the core package stays dependency-free and this module can be
imported, tested and read on a machine with no LiveKit installed. (Absolute
imports are the default in Python 3, so `from livekit.agents import ...` inside
this file resolves to the installed top-level package and never to this module.)

What the adapter does, and what it does not
-------------------------------------------
It refuses a phase transition on three grounds, in this order:

1. the speech handle carrying the tool call was already interrupted;
2. the transition is not the next legal one from the current phase;
3. the destination phase's entry conditions are not satisfied by the facts.

Only the third of those is a guarantee. The first is a race, in exactly the way
the framework's own `await utils.aio.cancel_and_wait(exe_task)` is a race
(`livekit/agents/voice/agent_activity.py:3611` in 1.7.1): a tool that finished
before cancellation reached it is committed regardless, and the comment above
that call says so. A tool body that mutates state synchronously will almost
always have finished.

The guarantee is in the facts. `FactsRecorder` writes `greeting_delivered` and
`pitch_delivered` only when a speech handle completes with
`SpeechHandle.interrupted` False (`speech_handle.py:108-110`). A turn that was
thrown away therefore leaves no evidence behind, so no later transition can
cite it. Timing decides *when* a legitimate transition is admitted. It cannot
decide *whether* an unearned one is.

That is the difference between admission and rollback, and it is why this
adapter does not try to undo a transition after the fact. See
`examples/livekit_guarded_agent.py` for why the rollback shape cannot be built
out of `FunctionToolsExecutedEvent.cancel_agent_handoff()` at all.
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional, Tuple
from weakref import WeakKeyDictionary

from .session import Facts, Phase, PhaseGuard

__all__ = [
    "Observation",
    "FactsRecorder",
    "guard_session",
    "guarded_transition",
    "recorder_for",
    "default_refusal",
    "livekit_agents_version",
    "SESSION_EVENTS",
]

# The two `AgentSession` events the recorder subscribes to. Both are members of
# `livekit.agents.voice.events.EventTypes` (events.py:287-303 in 1.7.1), which
# `FactsRecorder.attach` checks at subscription time.
SESSION_EVENTS = ("speech_created", "user_input_transcribed")

_RECORDERS: "WeakKeyDictionary[Any, FactsRecorder]" = WeakKeyDictionary()


# -- lazy access to the installed package ---------------------------------


def livekit_agents_version() -> Optional[str]:
    """The installed `livekit-agents` version, or None when it is not installed."""
    try:
        from livekit.agents import __version__
    except Exception:
        return None
    return str(__version__)


def _legal_event_names() -> Optional[frozenset]:
    """The event names `AgentSession.on` accepts, or None when LiveKit is absent.

    Used to fail loudly at `attach` time if an upstream rename ever silently
    detaches the recorder. A guard whose trigger is never wired is the fault
    this library exists to remove, so it is not left to a code review.
    """
    try:
        from typing import get_args

        from livekit.agents.voice.events import EventTypes
    except Exception:
        return None
    return frozenset(get_args(EventTypes))


# -- what the recorder saw -------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One thing the recorder observed, kept so a call can be audited afterwards.

    An empty observation log at the end of a call is the assertion that the
    wiring is live: it means no session event ever reached this recorder.
    """

    kind: str
    detail: str = ""
    phase: Optional[Phase] = None


class FactsRecorder:
    """Writes the guard's facts from observed session events, and owns the phase.

    Attach one per `AgentSession`. It subscribes to `speech_created` and
    `user_input_transcribed` (`SESSION_EVENTS`) and writes:

    `greeting_delivered`
        an agent speech created while the phase was GREETING completed with
        `SpeechHandle.interrupted` False.

    `pitch_delivered`
        the same, for a speech created while the phase was PITCH.

    `discovery_answers`
        the number of *completed* user turns observed while the phase was
        DISCOVERY, counted from `UserInputTranscribedEvent.is_final`
        (`events.py:323-331`).

    Two properties of that list matter more than its contents.

    First, the handler for `user_input_transcribed` never looks at
    `event.transcript`. It reads `is_final` and nothing else. No phrasing a
    caller can produce, and no instruction a model can be talked into, reaches
    the guard's inputs, because the utterance is not among them at any point in
    the chain.

    Second, counting completed user turns is a weaker signal than the reference
    implementation's `answers_a_discovery_question`, which is not observable
    from the event stream. It is deliberately the weaker one: recovering the
    stronger signal would mean classifying the transcript, which would put the
    utterance back into the guard's inputs. A conservative miscount produces a
    recoverable refusal; reading the utterance produces a bypass.

    A speech is credited to the phase it was *created* in, not the phase the
    session happens to be in when it finishes, so a transition admitted mid-turn
    cannot re-attribute the speech that preceded it.
    """

    def __init__(
        self,
        *,
        phase: Phase = Phase.GREETING,
        facts: Optional[Facts] = None,
        guard: Optional[PhaseGuard] = None,
    ) -> None:
        self._phase = phase
        self._facts = facts if facts is not None else Facts()
        self._guard = guard if guard is not None else PhaseGuard()
        self._observations: list = []
        self._session: Any = None
        self._subscriptions: list = []

    # -- read-only state ---------------------------------------------------
    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def facts(self) -> Facts:
        """The facts record. Frozen, and rebound rather than mutated on a write."""
        return self._facts

    @property
    def guard(self) -> PhaseGuard:
        return self._guard

    @property
    def observations(self) -> Tuple[Observation, ...]:
        return tuple(self._observations)

    @property
    def attached(self) -> bool:
        return self._session is not None

    @property
    def session(self) -> Any:
        return self._session

    # -- wiring ------------------------------------------------------------
    def attach(self, session: Any) -> "FactsRecorder":
        """Subscribe to `session` and register this recorder as its guard state.

        Raises RuntimeError if this recorder is already attached, if the session
        already has one, or if a subscribed event name is not one the installed
        `livekit-agents` emits.
        """
        if self._session is not None:
            raise RuntimeError("this FactsRecorder is already attached to a session")
        existing = _RECORDERS.get(session)
        if existing is not None:
            raise RuntimeError("that session already has a FactsRecorder attached")

        legal = _legal_event_names()
        if legal is not None:
            unknown = [name for name in SESSION_EVENTS if name not in legal]
            if unknown:
                raise RuntimeError(
                    "livekit-agents "
                    + str(livekit_agents_version())
                    + " does not emit "
                    + ", ".join(unknown)
                    + "; the facts recorder would never fire. Upgrade "
                    + "phantom-transition or pin livekit-agents to a supported release."
                )

        handlers = (
            ("speech_created", self._on_speech_created),
            ("user_input_transcribed", self._on_user_input_transcribed),
        )
        for name, handler in handlers:
            session.on(name, handler)
            self._subscriptions.append((name, handler))

        self._session = session
        _RECORDERS[session] = self
        return self

    def detach(self) -> None:
        """Unsubscribe. Safe to call on a recorder that was never attached."""
        session, self._session = self._session, None
        if session is None:
            return
        for name, handler in self._subscriptions:
            off = getattr(session, "off", None)
            if off is not None:
                off(name, handler)
        self._subscriptions = []
        if _RECORDERS.get(session) is self:
            del _RECORDERS[session]

    # -- admission ---------------------------------------------------------
    def check(self, target: Any) -> Tuple[bool, str]:
        """Ask the guard whether `target` is admissible now. Pure; commits nothing."""
        return self._guard.check(self._phase, target, self._facts)

    def admit(self, target: Any) -> Tuple[bool, str]:
        """Re-check and, if allowed, commit the transition. The only phase writer."""
        allowed, reason = self.check(target)
        if not allowed:
            self._observations.append(
                Observation("transition_refused", reason, self._phase)
            )
            return False, reason
        previous, self._phase = self._phase, target
        self._observations.append(
            Observation("transition_admitted", previous.name + "->" + target.name, target)
        )
        return True, reason

    # -- event handlers, the only writers of facts -------------------------
    def _on_speech_created(self, event: Any) -> None:
        handle = getattr(event, "speech_handle", None)
        if handle is None or not hasattr(handle, "add_done_callback"):
            return
        phase_at_creation = self._phase

        def _finished(finished_handle: Any) -> None:
            self._agent_speech_finished(finished_handle, phase_at_creation)

        handle.add_done_callback(_finished)

    def _agent_speech_finished(self, handle: Any, phase: Phase) -> None:
        if getattr(handle, "interrupted", False):
            self._observations.append(
                Observation("agent_speech_interrupted", str(getattr(handle, "id", "")), phase)
            )
            return
        self._observations.append(
            Observation("agent_speech_delivered", str(getattr(handle, "id", "")), phase)
        )
        if phase is Phase.GREETING:
            self._facts = replace(self._facts, greeting_delivered=True)
        elif phase is Phase.PITCH:
            self._facts = replace(self._facts, pitch_delivered=True)

    def _on_user_input_transcribed(self, event: Any) -> None:
        # `event.transcript` is deliberately not read. Only completion is.
        if not getattr(event, "is_final", False):
            return
        self._observations.append(Observation("user_turn_completed", "", self._phase))
        if self._phase is Phase.DISCOVERY:
            self._facts = replace(
                self._facts, discovery_answers=self._facts.discovery_answers + 1
            )


def guard_session(session: Any, *, recorder: Optional[FactsRecorder] = None) -> FactsRecorder:
    """Attach a `FactsRecorder` to `session` and return it. One line of adoption."""
    return (recorder if recorder is not None else FactsRecorder()).attach(session)


def recorder_for(session: Any) -> Optional[FactsRecorder]:
    """The recorder attached to `session`, or None."""
    try:
        return _RECORDERS.get(session)
    except TypeError:  # a session that cannot be weak-referenced or hashed
        return None


# -- the decorator ---------------------------------------------------------


def default_refusal(current: Phase, target: Any, reason: str) -> str:
    """The spoken string a refused transition returns to the model.

    A string rather than an exception, because a `ToolError`
    (`livekit/agents/llm/tool_context.py:122-137`) tells the model something
    broke. Nothing broke. The transition was considered and declined, and the
    model's next move should be to carry on talking, so it is told exactly that
    along with the reason and the phase it is still in. A plain string return
    leaves `reply_required` True (`voice/generation.py:1058-1066`), so the model
    gets its turn back.
    """
    name = target.name if isinstance(target, Phase) else repr(target)
    return (
        "The call is still in the "
        + current.name
        + " phase and did not move to "
        + name
        + ". Reason: "
        + reason
        + ". Do not call this tool again until that reason no longer holds. "
        + "Continue the conversation from "
        + current.name
        + "."
    )


def _reads(candidate: Any, name: str) -> bool:
    """Whether `candidate.name` can be read at all.

    `hasattr` is not enough. On a method tool the first argument is the `Agent`,
    and `Agent.session` raises `RuntimeError` when no activity is running
    (`voice/agent.py:477-482`), which `hasattr` propagates rather than swallows.
    Probing a tool's arguments must not be able to raise out of the guard.
    """
    try:
        getattr(candidate, name)
    except Exception:
        return False
    return True


def _find_run_context(args: tuple, kwargs: dict) -> Any:
    """Locate the `RunContext` among a tool's arguments, by shape rather than type.

    `RunContext` (`voice/events.py:45`) exposes `session` (:72-74) and
    `speech_handle` (:76-78). Matching on those two attributes rather than on
    `isinstance` is what lets the tests drive this adapter with fakes and no
    LiveKit server, and keeps `livekit.agents` out of the call path entirely.

    `speech_handle` is probed first because it is the discriminating one: an
    `Agent` bound as `self` has a `session` and no `speech_handle`.
    """
    for candidate in list(args) + list(kwargs.values()):
        if _reads(candidate, "speech_handle") and _reads(candidate, "session"):
            return candidate
    return None


def _interrupted(context: Any) -> bool:
    handle = getattr(context, "speech_handle", None)
    return bool(getattr(handle, "interrupted", False))


def _before(
    target: Any,
    args: tuple,
    kwargs: dict,
    recorder: Optional[FactsRecorder],
    refusal: Callable[[Phase, Any, str], str],
) -> Tuple[Optional[FactsRecorder], Any, Optional[str]]:
    """Everything checked before the wrapped body runs.

    Returns (recorder, context, refusal text or None).
    """
    context = _find_run_context(args, kwargs)
    if context is None:
        raise TypeError(
            "guarded_transition expects the tool to take a RunContext parameter; "
            "none of the arguments carried both `session` and `speech_handle`"
        )

    found = recorder if recorder is not None else recorder_for(getattr(context, "session", None))
    if found is None:
        # Fail closed. A guard that silently passes through when its state is
        # missing is the production failure this library exists to remove.
        return None, context, refusal(
            Phase.GREETING,
            target,
            "no facts recorder is attached to this session, so the entry evidence "
            "for this transition cannot be checked",
        )

    if _interrupted(context):
        return found, context, refusal(
            found.phase, target, "the turn that proposed this transition was interrupted"
        )

    allowed, reason = found.check(target)
    if not allowed:
        return found, context, refusal(found.phase, target, reason)

    return found, context, None


def _after(
    found: FactsRecorder,
    context: Any,
    target: Any,
    refusal: Callable[[Phase, Any, str], str],
) -> Optional[str]:
    """Everything checked after the body returns, before the transition commits."""
    if _interrupted(context):
        return refusal(
            found.phase,
            target,
            "the turn that proposed this transition was interrupted before it committed",
        )
    allowed, reason = found.admit(target)
    if not allowed:
        return refusal(found.phase, target, reason)
    return None


def guarded_transition(
    target: Phase,
    *,
    recorder: Optional[FactsRecorder] = None,
    refusal: Optional[Callable[[Phase, Any, str], str]] = None,
):
    """Gate a phase-advance function tool on turn completion and recorded facts.

    Apply it under `@function_tool()`, closest to the function::

        @function_tool()
        @guarded_transition(Phase.DISCOVERY)
        async def move_to_discovery(context: RunContext) -> str:
            return "Thanks. What brought you in today?"

    The wrapped body runs only if the transition is admissible. It must not
    write the phase itself: the adapter commits it, after the body returns and
    after one final interruption check, so that a barge-in landing while the
    body ran still leaves the phase where it was. A body that mutated the phase
    would reopen exactly that window.

    On refusal the body does not run and the tool returns a spoken string, so
    the model can recover and keep talking. Nothing raises.

    Order matters. Under `@function_tool()` this decorator sees the raw
    function, and `functools.wraps` carries `__name__`, `__doc__`,
    `__annotations__` and `__wrapped__` across, which is what
    `function_tool` needs to name the tool (`llm/tool_context.py:380`),
    describe it from the docstring (:381) and build its schema
    (`llm/utils.py:503-504`, `741-742`). Above `@function_tool()` it would be
    decorating a `FunctionTool` object instead, and the schema would be built
    from the wrong callable.

    Args:
        target: the phase this tool advances to.
        recorder: pin a specific recorder. By default the one attached to
            `context.session` is used, which is what `guard_session` registered.
        refusal: override the spoken refusal text. Called with
            `(current_phase, target, reason)`.
    """
    say_no = refusal if refusal is not None else default_refusal

    def decorate(func):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def guarded_async(*args, **kwargs):
                found, context, denial = _before(target, args, kwargs, recorder, say_no)
                if denial is not None:
                    return denial
                result = await func(*args, **kwargs)
                denial = _after(found, context, target, say_no)
                if denial is not None:
                    return denial
                return result

            return guarded_async

        @functools.wraps(func)
        def guarded_sync(*args, **kwargs):
            found, context, denial = _before(target, args, kwargs, recorder, say_no)
            if denial is not None:
                return denial
            result = func(*args, **kwargs)
            denial = _after(found, context, target, say_no)
            if denial is not None:
                return denial
            return result

        return guarded_sync

    return decorate
