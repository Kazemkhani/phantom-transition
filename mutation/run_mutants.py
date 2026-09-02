"""Mutation testing of session.py against four oracles.

A fixed list of source-level mutants is applied, one at a time, to a scratch
copy of src/phantom_transition/session.py. Each mutant is then run against:

  A  the published 24-test suite (test_guard.py, test_phantom_transition.py)
  B  the augmented suite: A plus tests/test_stateful_hypothesis.py
     (HYPOTHESIS_PROFILE=fast; one failing example kills a mutant)
  C  the frozen-oracle invariant checker (mutation/invariant_checker.py,
     five invariants, gates frozen into the checker, no differential)
  D  the state differential against the pristine module (same checker with
     --reference; skipped automatically when a mutant changes the model's
     shape, as the notebook exercise's HANDOFF mutant does)

A mutant is killed when any oracle fails. M3, M4 and M7 are credited to
results/core-v2 in the research hub (session c836e3, verified by the
orchestrator); they are re-run here so the kill table covers them, not to
re-derive the finding.

Usage: python mutation/run_mutants.py [--fast] [--output results/formal/mutation.md]
Exit code 0 when the baseline is green and every mutant is killed.
"""

from __future__ import annotations

import argparse
import datetime
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SESSION = "src/phantom_transition/session.py"

# Each mutant is a list of (pattern, replacement) pairs; every pattern must
# match exactly once or the run aborts.
MUTANTS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "backwards_check_removed": (
        "PhaseGuard.check no longer refuses target < current",
        [(r'        if target < current:\n            return False, "phase progression is forward only"\n',
          '        if False:\n            return False, "phase progression is forward only"\n')],
    ),
    "one_step_check_removed": (
        "PhaseGuard.check no longer refuses target != current + 1",
        [(r'        if target != current \+ 1:\n            return False, f"cannot skip from \{current\.name\} to \{target\.name\}"\n',
          '        if False:\n            return False, f"cannot skip from {current.name} to {target.name}"\n')],
    ),
    "entry_condition_check_removed": (
        "PhaseGuard.check no longer evaluates ENTRY_CONDITIONS",
        [(r'        if condition is not None and not condition\(facts\):\n',
          '        if False and condition is not None and not condition(facts):\n')],
    ),
    "interrupted_turns_keep_their_handoffs": (
        "the rollback of executed handoffs on an interrupted turn never runs",
        [(r'            if self\.cancel_handoff_on_interrupt:\n',
          '            if False and self.cancel_handoff_on_interrupt:\n')],
    ),
    "interrupted_turns_always_commit_tools": (
        "tool calls commit regardless of interruption or pre-emptive generation",
        [(r'        commits_tools = self\.preemptive_generation or not turn\.interrupted\n',
          '        commits_tools = True\n')],
    ),
    "facts_written_from_utterances": (
        "_record trusts a claim parsed out of the caller's utterance",
        [(r'    def _record\(self, turn: Turn\) -> None:\n        """Write structured facts from observed events, never from utterances\."""\n',
          '    def _record(self, turn: Turn) -> None:\n        """Write structured facts from observed events, never from utterances."""\n'
          '        if "pitch" in turn.utterance.lower():\n'
          '            self.facts = replace(self.facts, pitch_delivered=True)\n')],
    ),
    "handoff_admitted_without_consent": (
        "the notebook exercise's bug: HANDOFF added with a gate that ignores consent_recorded",
        [(r'    CLOSE = 3\n', '    CLOSE = 3\n    HANDOFF = 4\n'),
         (r'    pitch_delivered: bool = False\n',
          '    pitch_delivered: bool = False\n    consent_recorded: bool = False\n'),
         (r'    Phase\.CLOSE: lambda f: f\.pitch_delivered,\n',
          '    Phase.CLOSE: lambda f: f.pitch_delivered,\n'
          '    Phase.HANDOFF: lambda f: True,  # the planted bug: should be f.consent_recorded\n')],
    ),
    "m3_facts_recorded_on_interrupted_turns": (
        "core-v2 M3: _record dedented out of the completed-turn branch",
        [(r'        else:\n            emitted\.append\(Event\("spoke"\)\)\n            self\._record\(turn\)\n',
          '        else:\n            emitted.append(Event("spoke"))\n        self._record(turn)\n')],
    ),
    "m4_off_by_one_pitch_threshold": (
        "core-v2 M4: the PITCH gate weakened to one recorded answer",
        [(r'    Phase\.PITCH: lambda f: f\.discovery_answers >= 2,\n',
          '    Phase.PITCH: lambda f: f.discovery_answers >= 1,\n')],
    ),
    "m7_stale_count_recorded_in_pitch": (
        "core-v2 M7: an answer counter incremented while in PITCH",
        [(r'        elif self\.phase is Phase\.PITCH:\n            self\.facts = replace\(self\.facts, pitch_delivered=True\)\n',
          '        elif self.phase is Phase.PITCH:\n            self.facts = replace(self.facts, pitch_delivered=True, discovery_answers=self.facts.discovery_answers + 1)\n')],
    ),
}

PUBLISHED = ["tests/test_guard.py", "tests/test_phantom_transition.py"]
AUGMENTED = PUBLISHED + ["tests/test_stateful_hypothesis.py"]


def _env(tmp: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp / "src")
    env["HYPOTHESIS_PROFILE"] = "fast"
    env.pop("PHANTOM_RESULTS_DIR", None)
    return env


def pytest_oracle(tmp: Path, tests: list[str]) -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=tmp, capture_output=True, text=True, env=_env(tmp),
    )
    tail = next((l for l in reversed(r.stdout.splitlines()) if "passed" in l or "failed" in l or "error" in l), "")
    return r.returncode != 0, tail.strip()


def checker_oracle(tmp: Path, depth: int, differential: bool) -> tuple[bool, str]:
    cmd = [sys.executable, str(REPO / "mutation" / "invariant_checker.py"), "--src", str(tmp / "src"), "--depth", str(depth)]
    if differential:
        cmd += ["--reference", str(REPO / "src")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip().splitlines()
    if differential and any(l.startswith("SKIP differential") for l in out):
        return False, "skipped (model shape changed)"
    return r.returncode != 0, out[0] if out else ""


def make_copy(patches: list[tuple[str, str]] | None) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pt_mutant_"))
    shutil.copytree(REPO / "src", tmp / "src")
    shutil.copytree(REPO / "tests", tmp / "tests")
    if patches:
        target = tmp / SESSION
        text = target.read_text()
        for pattern, replacement in patches:
            text, n = re.subn(pattern, replacement, text)
            if n != 1:
                raise SystemExit(f"patch matched {n} times, expected 1: {pattern[:70]}")
        target.write_text(text)
    return tmp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="reduce the checker depth for CI")
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()
    depth = 6 if args.fast else 8

    rows = []
    tmp = make_copy(None)
    try:
        oracles = {
            "A published suite": pytest_oracle(tmp, PUBLISHED),
            "B augmented suite": pytest_oracle(tmp, AUGMENTED),
            "C frozen-oracle checker": checker_oracle(tmp, depth, differential=False),
            "D state differential": checker_oracle(tmp, depth, differential=True),
        }
    finally:
        shutil.rmtree(tmp)
    for name, (failed, tail) in oracles.items():
        print(f"baseline {name}: {'FAILED ' + tail if failed else 'green (' + tail + ')'}")
        if failed:
            print("baseline is not green; aborting")
            return 2

    for name, (description, patches) in MUTANTS.items():
        tmp = make_copy(patches)
        try:
            a = pytest_oracle(tmp, PUBLISHED)
            b = pytest_oracle(tmp, AUGMENTED)
            c = checker_oracle(tmp, depth, differential=False)
            d = checker_oracle(tmp, depth, differential=True)
        finally:
            shutil.rmtree(tmp)
        killed = a[0] or b[0] or c[0] or d[0]
        rows.append((name, description, a, b, c, d, killed))
        marks = "".join(o for o, (f, _) in zip("ABCD", (a, b, c, d)) if f)
        print(f"{name:42s} {'KILLED by ' + marks if killed else 'SURVIVES'}")

    kills = sum(1 for r in rows if r[6])
    total = len(rows)
    print(f"\nkill rate: {kills}/{total} ({kills / total:.0%}) with all four oracles")
    for label, idx in [("A", 2), ("B", 3), ("C", 4), ("D", 5)]:
        n = sum(1 for r in rows if r[idx][0])
        print(f"  oracle {label} alone: {n}/{total}")

    if args.output:
        write_report(args.output, rows, depth, args.fast)
    return 0 if kills == total else 1


def write_report(path: Path, rows, depth: int, fast: bool) -> None:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    lines = [
        "# Mutation testing of session.py",
        "",
        f"Generated by `python mutation/run_mutants.py{' --fast' if fast else ''} --output {path}`",
        f"on {datetime.date.today().isoformat()}, commit `{commit[:12]}`, Python {platform.python_version()},",
        f"checker depth {depth}. A mutant is killed when any oracle fails.",
        "",
        "Oracles: A the published 24-test suite; B adds tests/test_stateful_hypothesis.py",
        "(Hypothesis stateful invariants plus the deterministic regressions, fast profile);",
        "C the frozen-oracle invariant checker (five invariants, gates frozen into the",
        "checker); D the state differential against the pristine module (phase, facts and",
        "events compared move for move; skipped when a mutant changes the model's shape).",
        "",
        "M3, M4 and M7 were found by session c836e3 (results/core-v2 in the research hub,",
        "the load-bearing one verified independently by the orchestrator); they are re-run",
        "here for the kill table, not re-derived.",
        "",
        "| Mutant | A | B | C | D | Killed |",
        "|---|---|---|---|---|---|",
    ]
    for name, _desc, a, b, c, d, killed in rows:
        def mark(o):
            return "kills" if o[0] else ("skip" if "skipped" in o[1] else "blind")
        lines.append(f"| {name} | {mark(a)} | {mark(b)} | {mark(c)} | {mark(d)} | {'yes' if killed else 'NO'} |")
    kills = sum(1 for r in rows if r[6])
    lines += ["", f"Kill rate: {kills}/{len(rows)}.", ""]
    for name, desc, *_ in rows:
        lines.append(f"- `{name}`: {desc}")
    lines += [
        "",
        "## Findings",
        "",
        "1. `backwards_check_removed` and `one_step_check_removed` survive every test",
        "   suite that existed before this work and the frozen-oracle checker, and were",
        "   caught only by the state differential. They are masked mutants: a backward",
        "   target also fails the one-step check, and a skipped target also fails its",
        "   entry condition, because each gate's evidence can only be recorded in the",
        "   phase immediately before it. No admission decision changes; only the refusal",
        "   reason does. `test_refusal_reasons_name_the_violated_rule` now pins the",
        "   reasons, so oracle B kills both. The redundancy itself is a defence-in-depth",
        "   property of the guard worth stating, not a defect.",
        "2. `m3_facts_recorded_on_interrupted_turns` (the load-bearing core-v2 survivor)",
        "   is killed by the fifth invariant, in three independent forms: the Hypothesis",
        "   machine's `interrupted_turns_write_no_facts`, the deterministic regression",
        "   `test_an_interrupted_turn_writes_no_facts`, and the checker's I5. TLC catches",
        "   the same mutant at the specification level (formal/PhaseGuard_m3.cfg) while",
        "   proving the four original invariants blind to it (PhaseGuard_m3_blind.cfg).",
        "3. `m4_off_by_one_pitch_threshold` survives every oracle whose gate table is",
        "   derived from ENTRY_CONDITIONS, because a weakened gate satisfies its own",
        "   weakened condition. `test_one_recorded_answer_does_not_open_pitch` freezes",
        "   the threshold; the checker's frozen gates catch it independently.",
        "4. `m7_stale_count_recorded_in_pitch` changes no admission decision in this",
        "   model (the corrupted counter gates nothing after PITCH), which is why the",
        "   core-v2 phase-level equivalence search could not distinguish it to depth 6.",
        "   The facts record is public, audited state, so",
        "   `test_answers_are_not_recorded_while_in_pitch` pins it.",
        "5. `handoff_admitted_without_consent` (the notebook exercise's planted bug) is",
        "   killed only by the frozen-oracle checker, whose alphabet is built from the",
        "   module's own Phase and therefore exercises the extended model. No pytest in",
        "   the suite can know a phase that does not exist in the shipped model.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    print(f"report written to {path}")


if __name__ == "__main__":
    raise SystemExit(main())
