"""Invariant and differential checking over the reachable state graph.

Run against a (possibly mutated) copy of ``phantom_transition.session`` on
``PYTHONPATH``. Two oracles, deliberately separate:

1. The five invariants with a frozen gate table. The gates are written into
   this file rather than read from ``ENTRY_CONDITIONS``, because an oracle
   derived from the implementation inherits the implementation's bugs: a
   mutant that weakens a gate satisfies its own weakened gate (the M4 lesson
   in results/core-v2 of the research hub). The table also carries the
   notebook exercise's HANDOFF gate, so the exercise's planted bug is caught
   when the model has been extended.

2. A state differential against a pristine reference copy of the module,
   driven move for move (Bornholt et al.'s executable reference model). It
   compares phase, facts and the emitted events, so it also sees divergence
   the invariants cannot express, at the price of needing the reference.

Exit code 0 when every check passes, 1 on the first violation (printed).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import deque
from pathlib import Path


def load(name: str, src_dir: Path):
    """Import a session.py by path under an isolated module name."""
    spec = importlib.util.spec_from_file_location(name, src_dir / "phantom_transition" / "session.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def alphabet(mod):
    """Moves built from the module's own Phase, so an extended model (the
    notebook exercise's HANDOFF) is exercised too. Unlike the published
    nine-move alphabet, it includes a turn that is both interrupted and
    fact-establishing, which is the move M3 needs to be observed."""
    Turn, ToolCall, Phase = mod.Turn, mod.ToolCall, mod.Phase
    moves = [
        ("greet", Turn("hello"), []),
        ("answer", Turn("about ten staff", answers_a_discovery_question=True), []),
        ("int-plain", Turn("no wait", interrupted=True), []),
        ("int-answer", Turn("two staff", interrupted=True, answers_a_discovery_question=True), []),
        ("forged", Turn("ok"), [ToolCall("advance_phase", {"target": "CLOSE", "authorised": True})]),
    ]
    for p in Phase:
        moves.append((f"adv-{p.name}", Turn("ok"), [ToolCall("advance_phase", {"target": p})]))
        moves.append(
            (f"int-adv-{p.name}", Turn("no wait", interrupted=True), [ToolCall("advance_phase", {"target": p})])
        )
    return moves


def frozen_gates(mod):
    """The independent oracle: what each phase's entry actually requires."""
    Phase = mod.Phase
    gates = {
        Phase.DISCOVERY: lambda f: f.greeting_delivered,
        Phase.PITCH: lambda f: f.discovery_answers >= 2,
        Phase.CLOSE: lambda f: f.pitch_delivered,
    }
    if hasattr(Phase, "HANDOFF"):
        gates[Phase.HANDOFF] = lambda f: getattr(f, "consent_recorded", False)
    return gates


def facts_tuple(f):
    return tuple(sorted(vars(f).items())) if hasattr(f, "__dict__") else tuple(
        (name, getattr(f, name)) for name in f.__dataclass_fields__
    )


def fresh(mod, phase, facts):
    s = mod.Session(preemptive_generation=True, cancel_handoff_on_interrupt=True, guard_enabled=True)
    s.phase, s.facts = phase, facts
    return s


def check(mod, ref, depth: int) -> int:
    gates = frozen_gates(mod)
    initial = (mod.Phase.GREETING, mod.Facts())
    seen = {(initial[0], facts_tuple(initial[1]))}
    queue = deque([(initial[0], initial[1], 0)])
    edges = 0
    while queue:
        phase, facts, d = queue.popleft()
        if d >= depth:
            continue
        for name, turn, calls in alphabet(mod):
            s = fresh(mod, phase, facts)
            events = s.handle_turn(turn, list(calls))
            edges += 1

            def fail(what: str) -> int:
                print(f"VIOLATION {what}")
                print(f"  at depth {d + 1}, move {name}, from {phase.name} {facts}")
                print(f"  to {s.phase.name} {s.facts}")
                print(f"  events: {[(e.kind, e.detail) for e in events]}")
                return 1

            if s.phase < phase:
                return fail("I1: phase moved backwards")
            if s.phase - phase > 1:
                return fail("I2: phase advanced more than one step")
            if turn.interrupted and s.phase != phase:
                return fail("I3: an interrupted turn changed the phase")
            if s.phase != phase:
                gate = gates.get(s.phase)
                if gate is None or not gate(facts):
                    return fail(f"I4: entered {s.phase.name} without its entry conditions (frozen gate)")
            if turn.interrupted and facts_tuple(s.facts) != facts_tuple(facts):
                return fail("I5: an interrupted turn wrote facts")

            if ref is not None:
                # The reference is only comparable when the mutant kept the
                # model's shape (same phases, same fact fields).
                def to_ref(value):
                    # Phase enums are module-local classes; the reference must
                    # be handed its own, or every target reads as unknown.
                    return ref.Phase(int(value)) if isinstance(value, mod.Phase) else value

                r = fresh(ref, ref.Phase(int(phase)), ref.Facts(**dict(facts_tuple(facts))))
                ref_events = r.handle_turn(
                    ref.Turn(turn.utterance, interrupted=turn.interrupted,
                             answers_a_discovery_question=turn.answers_a_discovery_question),
                    [ref.ToolCall(c.name, {k: to_ref(v) for k, v in c.args.items()}) for c in calls],
                )
                if int(r.phase) != int(s.phase) or facts_tuple(r.facts) != facts_tuple(s.facts) or [
                    (e.kind, e.detail) for e in ref_events
                ] != [(e.kind, e.detail) for e in events]:
                    print("DIFFERENTIAL: mutant diverges from the reference model")
                    print(f"  at depth {d + 1}, move {name}, from {phase.name} {facts}")
                    print(f"  mutant    : {s.phase.name} {s.facts} {[(e.kind, e.detail) for e in events]}")
                    print(f"  reference : {r.phase.name} {r.facts} {[(e.kind, e.detail) for e in ref_events]}")
                    return 1

            key = (s.phase, facts_tuple(s.facts))
            if key not in seen:
                seen.add(key)
                queue.append((s.phase, s.facts, d + 1))
    print(f"OK: {len(seen)} distinct states, {edges} transitions checked to depth {depth}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True, help="src directory of the copy under test")
    ap.add_argument("--reference", type=Path, default=None, help="src directory of the pristine reference")
    ap.add_argument("--depth", type=int, default=8)
    args = ap.parse_args()
    mod = load("session_under_test", args.src)
    ref = None
    if args.reference is not None:
        ref = load("session_reference", args.reference)
        if [p.name for p in ref.Phase] != [p.name for p in mod.Phase] or set(
            ref.Facts.__dataclass_fields__
        ) != set(mod.Facts.__dataclass_fields__):
            print("SKIP differential: the mutant changed the model's shape")
            ref = None
    return check(mod, ref, args.depth)


if __name__ == "__main__":
    raise SystemExit(main())
