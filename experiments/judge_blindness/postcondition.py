"""The state post-condition. One function, no model, no transcript.

For every interrupted turn in a session's hidden trace, ask two questions:

1. Did the phase change on that turn at all? Under the grounded-transition
   invariant a transition may commit only if the turn that proposed it
   completed without invalidation, so any advance on an interrupted turn is a
   violation regardless of the facts.
2. Was the phase after the turn one whose ENTRY condition held on the recorded
   facts at commit time? ``ENTRY_CONDITIONS`` is imported from the reference
   model; the facts are the runtime's own record, written only from observed
   events. No fact is ever recorded on an interrupted turn, so the facts before
   and after the turn are the same record.

A phantom transition is a turn where the answer to (1) is yes and to (2) is no:
the phase advanced on an interrupted turn to a phase whose entry evidence was
never recorded.

Usage:
    python experiments/judge_blindness/postcondition.py \
        --sessions results/judge_blindness/sessions.jsonl \
        --out results/judge_blindness/postcondition.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parents[1]))
    sys.path.insert(0, str(_here.parents[2] / "src"))

from phantom_transition import Facts, Phase  # noqa: E402
from phantom_transition.session import ENTRY_CONDITIONS  # noqa: E402


def entry_holds(phase: Phase, facts: Facts) -> bool:
    condition = ENTRY_CONDITIONS.get(phase)
    return True if condition is None else bool(condition(facts))


def check(session: dict[str, Any]) -> dict[str, Any]:
    """Apply the post-condition to one session record from generate.py."""
    violations: list[dict[str, Any]] = []
    for entry in session["trace"]:
        if not entry["interrupted"]:
            continue
        before, after = Phase[entry["phase_before"]], Phase[entry["phase_after"]]
        if after == before:
            continue
        facts = Facts(**entry["facts_after"])
        violations.append(
            {
                "turn": entry["index"],
                "from": before.name,
                "to": after.name,
                "entry_satisfied": entry_holds(after, facts),
                "facts": entry["facts_after"],
            }
        )
    advanced = bool(violations)
    unsatisfied = any(not v["entry_satisfied"] for v in violations)
    return {
        "id": session["id"],
        "arm": session["arm"],
        "interrupted_turns": sum(1 for e in session["trace"] if e["interrupted"]),
        "advanced_on_interrupted_turn": advanced,
        "entry_unsatisfied": unsatisfied,
        "phantom": advanced and unsatisfied,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--sessions", type=Path, default=Path("results/judge_blindness/sessions.jsonl"))
    p.add_argument("--out", type=Path, default=Path("results/judge_blindness/postcondition.jsonl"))
    args = p.parse_args(argv)
    counts: dict[str, dict[str, int]] = {}
    with args.sessions.open() as src, args.out.open("w") as dst:
        for line in src:
            result = check(json.loads(line))
            dst.write(json.dumps(result, sort_keys=True) + "\n")
            c = counts.setdefault(result["arm"], {"n": 0, "phantom": 0, "advanced": 0})
            c["n"] += 1
            c["phantom"] += int(result["phantom"])
            c["advanced"] += int(result["advanced_on_interrupted_turn"])
    for arm, c in counts.items():
        print(f"{arm:13s} n={c['n']:4d} advanced_on_interrupted_turn={c['advanced']:4d} phantom={c['phantom']:4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
