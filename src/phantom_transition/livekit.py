"""Adopt the phase guard inside a LiveKit Agents session.

The core package knows nothing about any runtime. This module is the adapter
that puts the same guard on the tool boundary of a `livekit-agents` 1.7.x
`AgentSession`, in five lines of a user's own code.

Nothing here imports `livekit.agents` at module scope. Every import is inside a
function, so the core package stays dependency-free and this module can be
imported, tested and read on a machine with no LiveKit installed. (Absolute
imports are the default in Python 3, so `from livekit.agents import ...` inside
this file resolves to the installed top-level package and never to this module.)

Where the decision goes
-----------------------
The adapter refuses a phase transition on three grounds:

1. the speech handle carrying the tool call was already interrupted;
2. the transition is not the next legal one from the current phase;
3. the destination phase's entry conditions are not satisfied by the facts.

What matters more than the grounds is *when* the third one is asked. By default
the adapter does not decide inside the tool call at all. It stages the
transition against the speech handle that proposed it and decides when that
handle completes, in the same callback that writes the turn's facts, facts
first.

That is not a stylistic choice. Deciding inside the tool call is a race, in
exactly the way the framework's own `await utils.aio.cancel_and_wait(exe_task)`
is a race (`voice/agent_activity.py:3611`, whose comment two lines later says
the results of tools that finished are committed anyway). Cancellation closes
the window only while the tool is suspended, and a synchronous phase mutation
between two awaits is never suspended, so it is a race that has to be won every
single time. The survey in `results/core-v2/asyncio-interleaving.txt` of the
research repository measures it: a barge-in landing after the tool's effect has
landed is a phantom transition whether or not the tool is cancelled.

Deciding at completion asks a settled question instead. `SpeechHandle.interrupted`
read inside a done callback is final, because `interrupt()` returns early on a
handle that is already done (`speech_handle.py:195-197`). There is no window
left to lose.

It is also cheaper, not dearer. A transition whose entry condition is
established by its own turn, such as the greeting that licenses DISCOVERY, is
refused by an issue-time check and admitted by a completion-time one, because
the turn's facts are written first. The same repository's enumeration over all
9,834,496 four-turn sequences reports the issue-time design refusing 19.19% of
warranted transitions and violating the interruption invariant 80 times, and
the completion-time design doing neither.

`guarded_transition(..., commit="issue")` keeps the racy design available so
the two can be compared. It is not the one to use.

Underneath both sits the property that does not depend on timing at all: the
facts. `FactsRecorder` writes `greeting_delivered` and `pitch_delivered` only
when a speech handle completes with `SpeechHandle.interrupted` False
(`speech_handle.py:108-110`), so a turn that was thrown away leaves no evidence
behind for a later transition to cite, and a phantom transition cannot cascade.

This is why the adapter does not try to undo a transition after the fact. See
`examples/livekit_guarded_agent.py` for why the rollback shape cannot be built
out of `FunctionToolsExecutedEvent.cancel_agent_handoff()` at all.
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional, Tuple
from weakref import WeakKeyDictionary, WeakSet

from .session import Facts, Phase, PhaseGuard

__all__ = [
    "Observation",
    "FactsRecorder",
    "guard_session",
    "guarded_transition",
    "recorder_for",
    "default_refusal",
    "livekit_agents_version",
    "permissive_facts",
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


def permissive_facts() -> Facts:
    """The facts record that satisfies every entry condition there is.

    Used to ask a question the guard does not otherwise answer: *could any facts
    record admit this transition?* If not, the refusal is structural (a skip, a
    backward move, a target that is not a phase) and completing the turn cannot
    change it, so the model is told at once. If so, the only remaining objection
    is evidential, and evidence is exactly what the rest of the turn may still
    produce.

    Derived from the dataclass rather than written out, so a new field on
    `Facts` does not silently make this record less than maximal.
    `test_the_permissive_record_satisfies_every_entry_condition` is the check.
    """
    values = {}
    for field in dataclasses.fields(Facts):
        if isinstance(field.default, bool):
            values[field.name] = True
        elif isinstance(field.default, int):
            values[field.name] = 2**31
    return Facts(**values)


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

    The recorder also owns the commit point. A transition staged by
    `guarded_transition` is held against the speech handle that proposed it and
    resolved when that handle completes, in the same callback that writes the
    turn's facts, in that order. See `stage`.
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
        self._hooked: Any = WeakSet()
        self._staged: Any = WeakKeyDictionary()

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

    def check_structurally(self, target: Any) -> Tuple[bool, str]:
        """Ask whether *any* facts record could admit `target` from here.

        False means the objection is to the shape of the move, not to the
        evidence, so no amount of turn left to run can answer it.
        """
        return self._guard.check(self._phase, target, permissive_facts())

    def stage(self, handle: Any, target: Any) -> None:
        """Hold `target` against `handle`, to be resolved when the turn completes.

        This is the commit point, and it is the whole reason the adapter is not
        a race. `SpeechHandle.interrupted` read inside a done callback is final:
        `interrupt()` returns early on a handle that is already done
        (`speech_handle.py:195-197`), so a handle cannot become interrupted
        after its callbacks have run. Deciding there asks a settled question
        instead of a timing one.

        Resolving at completion is also what makes the guard free. A transition
        whose entry condition is established by its own turn (the greeting that
        licenses DISCOVERY is the obvious one) is refused by an issue-time
        check and admitted by this one, because the turn's facts are written
        first.
        """
        if getattr(handle, "done", None) is not None and handle.done():
            # Nothing left to wait for. The facts for this turn are already
            # written, so the question can be answered now.
            self._resolve(handle, target)
            return
        self._ensure_hooked(handle, self._phase)
        self._staged[handle] = target

    def staged_for(self, handle: Any) -> Any:
        """The transition waiting on `handle`, or None."""
        return self._staged.get(handle)

    def _resolve(self, handle: Any, target: Any) -> None:
        if getattr(handle, "interrupted", False):
            self._observations.append(
                Observation(
                    "transition_dropped",
                    "the turn that proposed it was interrupted",
                    self._phase,
                )
            )
            return
        self.admit(target)

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
        self._ensure_hooked(handle, self._phase)

    def _ensure_hooked(self, handle: Any, phase: Phase) -> None:
        """Register exactly one done callback on `handle`.

        One, not two, because `SpeechHandle._done_callbacks` is a `set`
        (`speech_handle.py:58`) iterated as `list(self._done_callbacks)`
        (`:61`), so two callbacks would run in an order nothing defines. The
        turn's facts must be written before a staged transition is judged
        against them, so both happen in one callback, in that order.
        """
        if handle in self._hooked:
            return
        self._hooked.add(handle)

        def _finished(finished_handle: Any) -> None:
            self._turn_finished(finished_handle, phase)

        handle.add_done_callback(_finished)

    def _turn_finished(self, handle: Any, phase: Phase) -> None:
        self._agent_speech_finished(handle, phase)
        target = self._staged.pop(handle, None)
        if target is not None:
            self._resolve(handle, target)

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
    commit: str,
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

    # At completion the evidential question is not asked yet: the turn's own act
    # may still establish it. Only the structural objections are answered now,
    # because no amount of turn left to run can answer those.
    check = found.check_structurally if commit == "completion" else found.check
    allowed, reason = check(target)
    if not allowed:
        return found, context, refusal(found.phase, target, reason)

    return found, context, None


def _after(
    found: FactsRecorder,
    context: Any,
    target: Any,
    refusal: Callable[[Phase, Any, str], str],
    commit: str,
) -> Optional[str]:
    """The commit, once the body has returned."""
    if commit == "completion":
        found.stage(getattr(context, "speech_handle", None), target)
        return None

    # commit == "issue": decide now, and re-read the interruption flag first.
    # This is the racy design, kept so it can be demonstrated and compared.
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
    commit: str = "completion",
):
    """Gate a phase-advance function tool on turn completion and recorded facts.

    Apply it under `@function_tool()`, closest to the function::

        @function_tool()
        @guarded_transition(Phase.DISCOVERY)
        async def move_to_discovery(context: RunContext) -> str:
            return "Thanks. What brought you in today?"

    The wrapped body runs only if the transition is structurally possible. It
    must not write the phase itself: the adapter owns the commit.

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
        commit: where the transition is decided.

            `"completion"` (the default) stages it against the speech handle
            that proposed it and decides when that handle completes, after the
            turn's facts have been written. `SpeechHandle.interrupted` is final
            by then, because `interrupt()` returns early on a handle that is
            already done (`speech_handle.py:195-197`), so there is no race left
            to lose. A transition whose entry condition is established by its
            own turn is admitted rather than refused.

            `"issue"` decides inside the tool call, re-reading the interruption
            flag immediately before it writes the phase. That check is a race in
            exactly the way the framework's own `cancel_and_wait` is a race, and
            it refuses transitions whose evidence the turn was about to
            establish. It is kept so the two can be compared, not because it is
            the one to use.
    """
    if commit not in ("completion", "issue"):
        raise ValueError("commit must be 'completion' or 'issue', not " + repr(commit))
    say_no = refusal if refusal is not None else default_refusal

    def decorate(func):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def guarded_async(*args, **kwargs):
                found, context, denial = _before(
                    target, args, kwargs, recorder, say_no, commit
                )
                if denial is not None:
                    return denial
                result = await func(*args, **kwargs)
                denial = _after(found, context, target, say_no, commit)
                if denial is not None:
                    return denial
                return result

            return guarded_async

        @functools.wraps(func)
        def guarded_sync(*args, **kwargs):
            found, context, denial = _before(
                target, args, kwargs, recorder, say_no, commit
            )
            if denial is not None:
                return denial
            result = func(*args, **kwargs)
            denial = _after(found, context, target, say_no, commit)
            if denial is not None:
                return denial
            return result

        return guarded_sync

    return decorate
