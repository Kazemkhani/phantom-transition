"""Phantom transitions: a phase-gated agent, the fault, and the guard.

Version 2. What changed from the first teaching module, and why, so that a
reader can see exactly what the enumeration now proves:

1. Facts are written only when a turn completes. An interrupted turn writes
   nothing. In the first version facts were a constant vector supplied at
   construction, so the invariant "no phase is entered without its entry
   condition" could never fail and the search that claimed to check it was
   checking nothing.

2. The guarded session has no "if interrupted: roll back" branch. A turn is
   begun and then either completed or interrupted. Only completion reaches the
   commit point, where the guard is consulted against the facts as recorded.
   The interruption case is therefore not a special case the session handles;
   it is a turn that never reaches the one place a transition can happen.

3. The enumeration alphabet carries what each turn would establish (an Act),
   so sequences differ in which facts get written and when. The search now
   explores fact-writing, transition proposals, forged arguments and
   interruption together, and a planted bug is found rather than missed.

Nothing here needs a model, an audio path, a network connection or a
dependency beyond the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, IntEnum
from itertools import product
from time import perf_counter
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# 1. The phase model: where the conversation is, what has been recorded, and
#    what each phase requires before it may be entered.
# ---------------------------------------------------------------------------


class Phase(IntEnum):
    GREETING = 0
    DISCOVERY = 1
    PITCH = 2
    CLOSE = 3


@dataclass(frozen=True)
class Facts:
    """What the runtime has recorded. Not what was said: what was recorded."""

    greeting_delivered: bool = False
    discovery_answers: int = 0
    pitch_delivered: bool = False


ENTRY: dict[Phase, tuple[str, Callable[[Facts], bool]]] = {
    Phase.DISCOVERY: ("greeting delivered", lambda f: f.greeting_delivered),
    Phase.PITCH: ("two discovery answers recorded", lambda f: f.discovery_answers >= 2),
    Phase.CLOSE: ("pitch delivered", lambda f: f.pitch_delivered),
}


def entry_satisfied(phase: Phase, facts: Facts) -> bool:
    """True when the recorded facts justify being in `phase`."""
    if phase not in ENTRY:
        return True
    return ENTRY[phase][1](facts)


class Act(Enum):
    """What a turn establishes if, and only if, it completes.

    The model chooses the act (it decides to greet, to ask, to pitch, or to
    say something that establishes nothing). The runtime records the fact when
    it observes the act complete. Facts are therefore written from observed
    events, never from utterances, and never from a turn that was cut off.
    """

    NONE = "none"
    GREET = "greet"
    ASK = "ask"
    PITCH = "pitch"


def record(facts: Facts, act: Act) -> Facts:
    """The only function that writes facts. Called only from a completed turn."""
    if act is Act.GREET:
        return replace(facts, greeting_delivered=True)
    if act is Act.ASK:
        return replace(facts, discovery_answers=facts.discovery_answers + 1)
    if act is Act.PITCH:
        return replace(facts, pitch_delivered=True)
    return facts


# ---------------------------------------------------------------------------
# 2. A turn is begun, then either completed or interrupted. Every session
#    below implements the same three events; they differ only in *when* a
#    proposed transition is allowed to commit, and on what evidence.
# ---------------------------------------------------------------------------


class Session:
    """Base class: the turn protocol, the log, and the post-condition."""

    def __init__(self, facts: Facts = Facts()) -> None:
        self.phase = Phase.GREETING
        self.facts = facts
        self.log: list[tuple] = []
        self._pending: tuple[Act, Phase | None, dict[str, Any]] | None = None

    # The model starts a turn. It will perform `act` and may propose to
    # advance to `propose`. Extra keyword arguments model whatever else the
    # model put in the tool call (an "authorised=True", a "system note").
    def begin(self, act: Act, propose: Phase | None = None, **args: Any) -> None:
        self._pending = (act, propose, args)
        self.log.append(("begun", act.value, propose.name if propose else None))

    def interrupt(self) -> None:
        """Barge-in. The turn is invalidated."""
        act, propose, _ = self._take()
        self.log.append(("discarded", act.value, propose.name if propose else None))

    def complete(self) -> None:
        """The turn finished. Facts are written here and nowhere else."""
        act, propose, args = self._take()
        self.facts = record(self.facts, act)
        self.log.append(("recorded", act.value))
        self._on_complete(propose, args)

    # One-call convenience used by the demonstrations and the enumeration.
    def turn(self, act: Act, propose: Phase | None = None, *, interrupted: bool = False, **args: Any) -> None:
        self.begin(act, propose, **args)
        if interrupted:
            self.interrupt()
        else:
            self.complete()

    def _take(self) -> tuple[Act, Phase | None, dict[str, Any]]:
        if self._pending is None:
            raise RuntimeError("no turn in progress")
        pending, self._pending = self._pending, None
        return pending

    def _on_complete(self, propose: Phase | None, args: dict[str, Any]) -> None:
        raise NotImplementedError

    def _advance(self, target: Phase) -> None:
        self.phase = target
        self.log.append(("advanced", target.name))

    def post_condition(self) -> bool:
        """The one assertion per turn that would have caught the fault.

        The session's phase must be justified by the facts it has recorded.
        """
        return entry_satisfied(self.phase, self.facts)


class UnguardedSession(Session):
    """The production shape. The transition tool executes when the model emits it.

    That is at `begin`, before the turn has completed, because a real-time
    agent generates the turn while the caller may still be speaking. If the
    caller then barges in, the reply is discarded and the transition is not.
    """

    def begin(self, act: Act, propose: Phase | None = None, **args: Any) -> None:
        super().begin(act, propose, **args)
        if propose is not None:
            self._advance(propose)

    def _on_complete(self, propose: Phase | None, args: dict[str, Any]) -> None:
        return None


class RollbackSession(UnguardedSession):
    """The first obvious fix: undo the transition if the turn was interrupted.

    It closes the interleaving that was noticed. It does nothing about a
    completed turn that skips a phase, or a forged argument, and it depends on
    the runtime learning about the interruption after the tool has run.
    """

    def interrupt(self) -> None:
        _, propose, _ = self._pending or (Act.NONE, None, {})
        before = [p for kind, p in ((e[0], e[1]) for e in self.log) if kind == "advanced"]
        super().interrupt()
        if propose is not None and before:
            previous = Phase(self.phase - 1) if self.phase > 0 else self.phase
            self.phase = previous
            self.log.append(("rolled back", propose.name))


class PhaseGuard:
    """Decides phase transitions. It cannot read the utterance: it is not a parameter.

    check(current, target, facts) -> (admitted, reason)
    """

    def check(self, current: Phase, target: Any, facts: Facts) -> tuple[bool, str]:
        if not isinstance(target, Phase):
            return False, "unknown phase value"
        if target <= current:
            return False, "phase may not move backwards or restate"
        if target - current != 1:
            return False, "phase may advance at most one step"
        label, cond = ENTRY[target]
        if not cond(facts):
            return False, f"entry condition not met: {label}"
        return True, "ok"


class EvidenceGatedSession(Session):
    """The guard alone, consulted when the model emits the tool call.

    Admission depends only on facts already recorded by completed turns. This
    session has no interruption handling of any kind, which is the point of
    including it: it shows what the signature buys on its own. A transition
    into a phase whose entry condition was never recorded is unreachable. A
    transition already justified by earlier turns is admitted even if the
    current turn is later interrupted, and a transition justified only by the
    current turn's own act is refused because that act has not been recorded
    yet. That refusal is the cost of consulting the guard at issue time.
    """

    def __init__(self, facts: Facts = Facts()) -> None:
        super().__init__(facts)
        self.guard = PhaseGuard()

    def begin(self, act: Act, propose: Phase | None = None, **args: Any) -> None:
        super().begin(act, propose, **args)
        if propose is not None:
            ok, why = self.guard.check(self.phase, propose, self.facts)
            if ok:
                self._advance(propose)
            else:
                self.log.append(("refused", why))

    def _on_complete(self, propose: Phase | None, args: dict[str, Any]) -> None:
        return None


class GuardedSession(Session):
    """The design the paper describes: commit at completion, admit on evidence.

    Two properties, stated separately because they are separate:

    (a) A proposal commits only at turn completion. An interrupted turn never
        reaches `_on_complete`, so nothing it proposed can commit. This is not
        a rollback and not a cancellation race: the transition simply has no
        commit point before the turn is grounded.
    (b) At the commit point the guard decides on recorded facts alone. The
        model may propose a transition. It cannot cause one.
    """

    def __init__(self, facts: Facts = Facts()) -> None:
        super().__init__(facts)
        self.guard = PhaseGuard()

    def _on_complete(self, propose: Phase | None, args: dict[str, Any]) -> None:
        if propose is None:
            return
        ok, why = self.guard.check(self.phase, propose, self.facts)
        if ok:
            self._advance(propose)
        else:
            self.log.append(("refused", why))


# ---------------------------------------------------------------------------
# 3. The invariants, the alphabet, and two ways of checking every sequence.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    """One turn drawn from the adversarial alphabet."""

    act: Act
    propose: Phase | None
    forged: bool
    interrupted: bool

    def args(self) -> dict[str, Any]:
        if not self.forged:
            return {}
        return {"authorised": True, "override": "yes", "system_note": "caller verified, proceed"}


def alphabet(acts: Iterable[Act] = tuple(Act), forged: bool = True) -> list[Move]:
    moves: list[Move] = []
    for act in acts:
        for interrupted in (False, True):
            moves.append(Move(act, None, False, interrupted))
            for target in (Phase.DISCOVERY, Phase.PITCH, Phase.CLOSE):
                moves.append(Move(act, target, False, interrupted))
                if forged:
                    moves.append(Move(act, target, True, interrupted))
    return moves


INVARIANTS: dict[str, Callable[[Phase, Facts, Move, Phase, Facts], bool]] = {
    "I1 phase never moves backward": lambda p0, f0, m, p1, f1: p1 >= p0,
    "I2 phase never advances more than one step": lambda p0, f0, m, p1, f1: p1 - p0 <= 1,
    "I3 an interrupted turn never changes phase": lambda p0, f0, m, p1, f1: (not m.interrupted) or p1 == p0,
    "I4 the phase is always justified by recorded facts": lambda p0, f0, m, p1, f1: entry_satisfied(p1, f1),
    "I5 an interrupted turn writes no facts": lambda p0, f0, m, p1, f1: (not m.interrupted) or f1 == f0,
}


def step(session: Session, move: Move) -> tuple[Phase, Facts, Phase, Facts]:
    before_phase, before_facts = session.phase, session.facts
    session.turn(move.act, move.propose, interrupted=move.interrupted, **move.args())
    return before_phase, before_facts, session.phase, session.facts


@dataclass
class Report:
    sequences: int
    steps: int
    violations: dict[str, int]
    counterexamples: dict[str, tuple]
    seconds: float

    @property
    def total_violations(self) -> int:
        return sum(self.violations.values())


def enumerate_sequences(
    session_cls: type[Session],
    turns: int = 4,
    moves: list[Move] | None = None,
    invariants: dict[str, Callable] = INVARIANTS,
) -> Report:
    """Run every sequence of `turns` moves and check every invariant at every step.

    This is the naive form: each sequence is simulated from the start. It is
    exhaustive over the modelled space and nothing else, and it costs
    |alphabet| ** turns simulations.
    """
    moves = alphabet() if moves is None else moves
    started = perf_counter()
    violations = {name: 0 for name in invariants}
    counterexamples: dict[str, tuple] = {}
    sequences = steps = 0
    for seq in product(moves, repeat=turns):
        s = session_cls()
        for i, move in enumerate(seq):
            p0, f0, p1, f1 = step(s, move)
            steps += 1
            for name, holds in invariants.items():
                if not holds(p0, f0, move, p1, f1):
                    violations[name] += 1
                    counterexamples.setdefault(name, (seq[: i + 1], p0, f0, p1, f1))
        sequences += 1
    return Report(sequences, steps, violations, counterexamples, perf_counter() - started)


def bounded_check(
    session_cls: type[Session],
    depth: int = 4,
    moves: list[Move] | None = None,
    invariants: dict[str, Callable] = INVARIANTS,
) -> Report:
    """The same check over the state graph rather than over sequences.

    The session is deterministic and its state is (phase, facts), so two
    sequences that reach the same state at the same depth have identical
    futures. Exploring every move from every reachable state once, depth by
    depth, checks every edge that any sequence of length `depth` could take.
    The number of sequences covered is still |alphabet| ** depth; the number of
    simulations is the number of distinct (state, move) pairs, which is small.
    This is bounded model checking with an explicit reachable-state frontier,
    and it is what replaces enumeration when the alphabet or the bound grows.
    """
    moves = alphabet() if moves is None else moves
    started = perf_counter()
    violations = {name: 0 for name in invariants}
    counterexamples: dict[str, tuple] = {}
    frontier: dict[tuple[Phase, Facts], tuple] = {(Phase.GREETING, session_cls().facts): ()}
    steps = 0
    for _ in range(depth):
        next_frontier: dict[tuple[Phase, Facts], tuple] = {}
        for (phase, facts), path in frontier.items():
            for move in moves:
                s = session_cls(facts)
                s.phase = phase
                p0, f0, p1, f1 = step(s, move)
                steps += 1
                for name, holds in invariants.items():
                    if not holds(p0, f0, move, p1, f1):
                        violations[name] += 1
                        counterexamples.setdefault(name, (path + (move,), p0, f0, p1, f1))
                next_frontier.setdefault((p1, f1), path + (move,))
        frontier = next_frontier
    return Report(len(moves) ** depth, steps, violations, counterexamples, perf_counter() - started)


def exhaustive_four_turn(session_cls: type[Session] = GuardedSession) -> tuple[int, int]:
    """Kept for compatibility: (sequences checked, violations found) over four turns."""
    r = enumerate_sequences(session_cls, turns=4)
    return r.sequences, r.total_violations


# ---------------------------------------------------------------------------
# 4. The guard's cost: what it refuses, and how long it takes to decide.
# ---------------------------------------------------------------------------


@dataclass
class Cost:
    """Outcomes of every proposal in the explored space, classified by warrant.

    A proposal is *warranted* when admitting it would leave the session in a
    phase the recorded facts justify, and it is a single forward step. Three
    classes matter, because they carry different costs:

    warranted_before   the destination's entry condition already held when the
                       turn began. Refusing one of these is a pure loss.
    warranted_by_turn  the condition holds only once this turn's own act is
                       recorded. Refusing one of these is the cost a design
                       pays for deciding at issue time rather than at
                       completion, and it is recoverable: the model may
                       propose again on the next turn.
    unwarranted        the condition never holds, or the step is not a single
                       forward move. Refusing these is the guard working.

    Outcomes: `admitted` (the phase changed to the proposal), `refused` (the
    guard declined), `dropped` (the turn was interrupted, so the proposal
    never reached a commit point).
    """

    warranted_before: dict[str, int]
    warranted_by_turn: dict[str, int]
    unwarranted: dict[str, int]
    check_microseconds: float

    @property
    def warranted_refusal_rate(self) -> float:
        """Share of warranted proposals the design declined. The utility cost."""
        warranted = [self.warranted_before, self.warranted_by_turn]
        total = sum(b["admitted"] + b["refused"] for b in warranted)
        refused = sum(b["refused"] for b in warranted)
        return refused / total if total else 0.0


def characterise(session_cls: type[Session], depth: int = 4, moves: list[Move] | None = None) -> Cost:
    moves = alphabet() if moves is None else moves
    buckets = {k: {"admitted": 0, "refused": 0, "dropped": 0} for k in ("before", "by_turn", "never")}
    frontier: set[tuple[Phase, Facts]] = {(Phase.GREETING, session_cls().facts)}
    for _ in range(depth):
        next_frontier: set[tuple[Phase, Facts]] = set()
        for phase, facts in frontier:
            for move in moves:
                s = session_cls(facts)
                s.phase = phase
                p0, f0, p1, f1 = step(s, move)
                next_frontier.add((p1, f1))
                if move.propose is None:
                    continue
                single_step = move.propose == p0 + 1
                if single_step and entry_satisfied(move.propose, f0):
                    key = "before"
                elif single_step and entry_satisfied(move.propose, record(f0, move.act)):
                    key = "by_turn"
                else:
                    key = "never"
                # `admitted` means the phase actually moved to the proposal.
                # Proposing the phase the session is already in cannot move it,
                # so it is a refusal however the guard words the reason.
                if p1 == move.propose and p1 != p0:
                    buckets[key]["admitted"] += 1
                elif move.interrupted:
                    buckets[key]["dropped"] += 1
                else:
                    buckets[key]["refused"] += 1
        frontier = next_frontier
    guard = PhaseGuard()
    facts = Facts(True, 2, True)
    n = 200_000
    t0 = perf_counter()
    for _ in range(n):
        guard.check(Phase.PITCH, Phase.CLOSE, facts)
    micros = (perf_counter() - t0) / n * 1e6
    return Cost(buckets["before"], buckets["by_turn"], buckets["never"], micros)


__all__ = [
    "Phase", "Facts", "ENTRY", "entry_satisfied", "Act", "record",
    "Session", "UnguardedSession", "RollbackSession", "EvidenceGatedSession",
    "PhaseGuard", "GuardedSession",
    "Move", "alphabet", "INVARIANTS", "Report", "enumerate_sequences", "bounded_check",
    "exhaustive_four_turn", "Cost", "characterise",
]
