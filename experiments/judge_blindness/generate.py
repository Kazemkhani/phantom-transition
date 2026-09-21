"""Deterministic generator of synthetic phase-gated sessions with a hidden state trace.

Each session is an outbound qualification call (greeting, discovery, pitch,
close) whose state is driven by the reference model in
``phantom_transition.session``: every agent utterance is rendered from the
session's (phase, facts) by a fixed policy, every caller turn is fed through
``Session.handle_turn``, and the resulting phase and facts are recorded after
each exchange. The transcript is therefore a function of the state, which is
what makes the hidden trace a ground truth rather than an annotation.

Three arms share every random draw for a given (seed, index), so a phantom
session and its control differ only in the runtime configuration and, as a
consequence, in the agent's recovery utterance:

phantom
    ``Session(preemptive_generation=True, cancel_handoff_on_interrupt=False,
    guard_enabled=False)``. On the injection exchange the model has emitted a
    speculative phase advance during the utterance the caller then interrupts.
    The speech is discarded, the tool call commits, and the next agent
    utterance is rendered from the advanced phase. The destination's entry
    condition does not hold on the recorded facts.
control
    ``Session(preemptive_generation=True, cancel_handoff_on_interrupt=True,
    guard_enabled=True)``. Same script, same speculative tool call, same
    interruption. The guard refuses the unjustified advance and any justified
    one is rolled back because the turn was interrupted. The recovery is
    rendered from the unchanged phase.
bad_recovery
    Control state, but the agent ignores the interruption and continues the
    cut-off sentence verbatim. A positive control for the judge: a visible
    recovery fault that a recovery-quality rubric should catch.

Injected transitions (drawn per session):
    GREETING->DISCOVERY   the greeting itself is interrupted, so
                          greeting_delivered was never recorded
    DISCOVERY->PITCH (0)  the first discovery question carries both the
                          legitimate advance to DISCOVERY and a speculative
                          advance to PITCH; zero answers recorded
    DISCOVERY->PITCH (1)  the second discovery question carries a speculative
                          advance to PITCH; one answer recorded
    PITCH->CLOSE          the pitch opener carries the legitimate advance to
                          PITCH and a speculative advance to CLOSE; the pitch
                          was never delivered

Usage:
    python experiments/judge_blindness/generate.py --seed 20260902 --n 200 \
        --out results/judge_blindness/sessions.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parents[1]))
    sys.path.insert(0, str(_here.parents[2] / "src"))

from judge_blindness import bank  # noqa: E402
from phantom_transition import Facts, Phase, Session, ToolCall, Turn  # noqa: E402

ARMS = ("phantom", "control", "bad_recovery")
TRANSITIONS = (
    "GREETING->DISCOVERY",
    "DISCOVERY->PITCH (0 answers)",
    "DISCOVERY->PITCH (1 answer)",
    "PITCH->CLOSE",
)
MAX_EXCHANGES = 12


@dataclass
class Exchange:
    index: int
    kind: str
    phase_at_start: str
    agent_full: str
    tool_calls: list[str]
    caller: str
    interrupted: bool
    cut_words: int | None
    answers_discovery: bool
    interruption_type: str | None = None


@dataclass
class TraceEntry:
    index: int
    phase_before: str
    phase_after: str
    facts_before: dict[str, Any]
    facts_after: dict[str, Any]
    interrupted: bool
    tool_calls: list[str]
    events: list[str]


@dataclass
class Plan:
    """Every random draw for one session index, made before the arm is known."""

    agent: str
    caller: str
    vertical: dict[str, Any]
    company: str
    caller_company: str
    categories: list[str]
    team_size: int
    budget: str
    day: str
    time: str
    transition: str
    interruption_type: str
    interruption: dict[str, Any]
    cut_fraction: float
    picks: dict[str, int] = field(default_factory=dict)


def session_id(seed: int, arm: str, index: int, variant: str = "", source: str = "") -> str:
    """Opaque identifier. Reveals neither the arm nor the index to a judge."""
    key = f"{seed}:{arm}:{index}" + (f":{variant}" if variant else "") + (f":{source}" if source else "")
    digest = hashlib.sha1(key.encode()).hexdigest()
    return "s" + digest[:10]


def _draw_plan(seed: int, index: int, transition_override: str | None = None) -> Plan:
    rng = random.Random(f"judge-blindness:{seed}:{index}")
    vertical = rng.choice(bank.VERTICALS)
    transition = transition_override or rng.choice(TRANSITIONS)
    categories = rng.sample(list(bank.DISCOVERY), 2)
    # Corrections need a prior discovery answer to correct, so they are only
    # available once one has been recorded.
    answered_before_injection = {
        "GREETING->DISCOVERY": [],
        "DISCOVERY->PITCH (0 answers)": [],
        "DISCOVERY->PITCH (1 answer)": categories[:1],
        "PITCH->CLOSE": categories,
    }[transition]
    types = ["normal", "topic_switch", "pushback"]
    if answered_before_injection:
        types.append("correction")
    interruption_type = rng.choice(types)
    candidates = bank.INTERRUPTIONS[interruption_type]
    if interruption_type == "correction":
        candidates = [c for c in candidates if c["requires_answer"] in answered_before_injection]
    interruption = rng.choice(candidates)
    team_size = rng.choice(bank.TEAM_SIZES)
    picks = {
        "greeting": rng.randrange(len(bank.GREETINGS)),
        "greeting_retry": rng.randrange(len(bank.GREETINGS_RETRY)),
        "confirm": rng.randrange(len(bank.CALLER_CONFIRMS)),
        "pitch": rng.randrange(len(bank.PITCHES)),
        "pitch_retry": rng.randrange(len(bank.PITCHES_RETRY)),
        "react": rng.randrange(len(bank.CALLER_REACTS_TO_PITCH)),
        "close": rng.randrange(len(bank.CLOSES)),
        "close_retry": rng.randrange(len(bank.CLOSES_RETRY)),
        "agree": rng.randrange(len(bank.CALLER_AGREES)),
        "confirmation": rng.randrange(len(bank.CONFIRMATIONS)),
        "goodbye": rng.randrange(len(bank.CALLER_GOODBYES)),
        "address": rng.randrange(len(interruption["address"])),
    }
    for cat in categories:
        picks[f"q_{cat}_first"] = rng.randrange(len(bank.DISCOVERY[cat]["first"]))
        picks[f"q_{cat}_retry"] = rng.randrange(len(bank.DISCOVERY[cat]["retry"]))
        picks[f"a_{cat}"] = rng.randrange(len(bank.DISCOVERY[cat]["answers"]))
    plan = Plan(
        agent=rng.choice(bank.AGENT_NAMES),
        caller=rng.choice(bank.CALLER_NAMES),
        vertical=vertical,
        company=rng.choice(vertical["companies"]),
        caller_company=rng.choice(vertical["caller_companies"]),
        categories=categories,
        team_size=team_size,
        budget=rng.choice(bank.BUDGETS),
        day=rng.choice(bank.DAYS),
        time=rng.choice(bank.TIMES),
        transition=transition,
        interruption_type=interruption_type,
        interruption=interruption,
        cut_fraction=rng.uniform(0.35, 0.70),
        picks=picks,
    )
    # Drawn last so that adding it did not shift any draw of the main corpus.
    plan.picks["acknowledge"] = rng.randrange(len(bank.CALLER_ACKNOWLEDGES))
    return plan


class Renderer:
    """Fills templates from a plan. Pure; no randomness after the plan is drawn."""

    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        v = plan.vertical
        self.fields = {
            "agent": plan.agent,
            "caller": plan.caller,
            "company": plan.company,
            "caller_company": plan.caller_company,
            "vertical": v["name"],
            "staff": v["staff"],
            "enquiry": v["enquiry"],
            "pain": v["pain"].format(staff=v["staff"]),
            "offer": v["offer"].format(staff=v["staff"]),
            "proof": v["proof"],
            "day": plan.day,
            "time": plan.time,
            "n": plan.team_size,
            "m": plan.team_size + (6 if plan.team_size < 20 else 10),
            "budget": plan.budget,
        }

    def fill(self, template: str) -> str:
        return template.format(**self.fields)

    def system_prompt(self) -> str:
        return self.fill(bank.SYSTEM_PROMPT)

    def question(self, cat: str, retry: bool) -> str:
        pool = bank.DISCOVERY[cat]["retry" if retry else "first"]
        return self.fill(pool[self.plan.picks[f"q_{cat}_{'retry' if retry else 'first'}"]])

    def answer(self, cat: str) -> str:
        return self.fill(bank.DISCOVERY[cat]["answers"][self.plan.picks[f"a_{cat}"]])

    def pick(self, pool: list[str], key: str) -> str:
        return self.fill(pool[self.plan.picks[key]])

    def address(self) -> str:
        return self.fill(self.plan.interruption["address"][self.plan.picks["address"]])

    def interruption(self) -> str:
        return self.fill(self.plan.interruption["text"])


def _policy(phase: Phase, facts: Facts, r: Renderer, retry_kind: str | None) -> tuple[str, str, list[Phase], str]:
    """What the agent in ``phase`` with ``facts`` says next.

    Returns (kind, utterance, tool_calls, expected_caller_kind). ``retry_kind``
    is the kind of the utterance that was just interrupted; the same kind is
    rendered as a fresh-start rephrasing rather than the first-ask wording.
    """
    plan = r.plan
    cats = plan.categories

    def q(i: int) -> tuple[str, str, list[Phase], str]:
        kind = f"discovery_{i}"
        return kind, r.question(cats[i], retry_kind == kind), [], "answer"

    if phase is Phase.GREETING:
        if not facts.greeting_delivered:
            retry = retry_kind == "greeting"
            text = r.pick(bank.GREETINGS_RETRY, "greeting_retry") if retry else r.pick(bank.GREETINGS, "greeting")
            return "greeting", text, [], "confirm"
        kind, text, _, expect = q(0)
        return kind, text, [Phase.DISCOVERY], expect
    # Pitch and close recoveries always use the neutral fresh-start family,
    # whichever kind was interrupted: the first-ask wording thanks the caller
    # for discovery answers or reacts to the pitch, which would give the two
    # arms different surface cues that have nothing to do with the state.
    recovering = retry_kind is not None
    if phase is Phase.DISCOVERY:
        if facts.discovery_answers < 2:
            return q(facts.discovery_answers)
        text = r.pick(bank.PITCHES_RETRY, "pitch_retry") if recovering else r.pick(bank.PITCHES, "pitch")
        return "pitch", text, [Phase.PITCH], "react"
    if phase is Phase.PITCH:
        if not facts.pitch_delivered:
            text = r.pick(bank.PITCHES_RETRY, "pitch_retry") if recovering else r.pick(bank.PITCHES, "pitch")
            return "pitch", text, [], "react"
        text = r.pick(bank.CLOSES_RETRY, "close_retry") if recovering else r.pick(bank.CLOSES, "close")
        return "close", text, [Phase.CLOSE], "agree"
    return "confirmation", r.pick(bank.CONFIRMATIONS, "confirmation"), [], "goodbye"


def _caller(expect: str, r: Renderer, kind: str) -> tuple[str, bool]:
    """The caller's cooperative reply to an agent utterance of the given kind."""
    if expect == "confirm":
        return r.pick(bank.CALLER_CONFIRMS, "confirm"), False
    if expect == "answer":
        i = int(kind.split("_")[1])
        return r.answer(r.plan.categories[i]), True
    if expect == "react":
        return r.pick(bank.CALLER_REACTS_TO_PITCH, "react"), False
    if expect == "agree":
        return r.pick(bank.CALLER_AGREES, "agree"), False
    if expect == "acknowledge":
        return r.pick(bank.CALLER_ACKNOWLEDGES, "acknowledge"), False
    return r.pick(bank.CALLER_GOODBYES, "goodbye"), False


def _injection(kind: str, tools: list[Phase], transition: str) -> list[Phase] | None:
    """The speculative advance the model emits on the injection exchange, or None."""
    if transition == "GREETING->DISCOVERY" and kind == "greeting":
        return tools + [Phase.DISCOVERY]
    if transition == "DISCOVERY->PITCH (0 answers)" and kind == "discovery_0":
        return tools + [Phase.PITCH]
    if transition == "DISCOVERY->PITCH (1 answer)" and kind == "discovery_1":
        return tools + [Phase.PITCH]
    if transition == "PITCH->CLOSE" and kind == "pitch":
        return tools + [Phase.CLOSE]
    return None


def _cut(text: str, fraction: float) -> int:
    words = text.split()
    n = max(3, min(len(words) - 3, round(len(words) * fraction)))
    return n


def heard(text: str, cut_words: int | None) -> str:
    if cut_words is None:
        return text
    return " ".join(text.split()[:cut_words])


def remainder(text: str, cut_words: int) -> str:
    return " ".join(text.split()[cut_words:])


def _facts(f: Facts) -> dict[str, Any]:
    return asdict(f)


VARIANTS = ("", "deferred")
STATE_SOURCES = ("model", "asyncio")


def build_session(seed: int, arm: str, index: int, variant: str = "", state_source: str = "model") -> dict[str, Any]:
    """Build one session.

    ``variant=""`` is the main design: the recovery utterance addresses the
    interruption and, in the same turn, continues with the agent's next move,
    so the destination phase's behaviour is visible in the response under
    evaluation. ``variant="deferred"`` splits that into two turns: the
    recovery utterance is the address alone, the caller acknowledges it, and
    the phase-specific utterance follows on the next agent turn, which a
    per-turn interruption-recovery judge never scores. In the deferred variant
    the response under evaluation is identical across the phantom and control
    arms by construction; the state trace is not.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm!r}")
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant!r}")
    if state_source not in STATE_SOURCES:
        raise ValueError(f"unknown state source: {state_source!r}")
    # The asyncio reproduction models one speculative advance out of GREETING,
    # so asyncio-sourced sessions are restricted to that transition.
    plan = _draw_plan(seed, index, "GREETING->DISCOVERY" if state_source == "asyncio" else None)
    r = Renderer(plan)
    fixed = arm != "phantom"
    session = Session(
        preemptive_generation=True,
        cancel_handoff_on_interrupt=fixed,
        guard_enabled=fixed,
    )
    exchanges: list[Exchange] = []
    trace: list[TraceEntry] = []
    injected_at: int | None = None
    retry_kind: str | None = None
    pending_bad: str | None = None
    recovering = False       # this exchange is the recovery utterance
    deferred_move = False    # this exchange is the phase move after a deferred address

    while len(exchanges) < MAX_EXCHANGES:
        phase, facts = session.phase, session.facts
        kind, text, tools, expect = _policy(phase, facts, r, retry_kind)
        interrupted = False
        cut_words: int | None = None
        interruption_type: str | None = None

        if injected_at is None:
            spec = _injection(kind, tools, plan.transition)
        else:
            spec = None

        if spec is not None:
            tools = spec
            interrupted = True
            cut_words = _cut(text, plan.cut_fraction)
            caller, answers = r.interruption(), False
            interruption_type = plan.interruption_type
            injected_at = len(exchanges)
            if arm == "bad_recovery":
                pending_bad = remainder(text, cut_words)
        else:
            if recovering:
                # Recovery utterance: address the interruption, then a fresh start.
                if arm == "bad_recovery" and pending_bad is not None:
                    text = pending_bad
                    pending_bad = None
                elif variant == "deferred":
                    # Address only; the agent's next move waits for the next turn.
                    text = r.address()
                    tools = []
                    expect = "acknowledge"
                else:
                    text = f"{r.address()} {text}"
                kind = f"recovery:{kind}"
            elif deferred_move:
                # The turn after a deferred address: the phase-specific move, as a
                # fresh start, rendered from whatever phase the runtime is in now.
                kind = f"deferred:{kind}"
            caller, answers = _caller(expect, r, kind.split(":")[-1])

        if state_source == "asyncio" and interrupted:
            # The injection turn's state transition comes from a real event
            # loop rather than from the reference model: the vendored asyncio
            # reproduction (interleaving_v2, from branch amir/science-v2) runs
            # speculative generation, a spawned tool task and a barge-in
            # through asyncio's own scheduler. Phantom arm: the barge-in lands
            # after the effect, so even cancelling the tool with the reply
            # leaves the phase advanced. Control arm: it lands before the
            # effect and the cancellation works.
            from judge_blindness.interleaving_v2 import Stage, reproduce

            stage = Stage.AFTER_EFFECT if arm == "phantom" else Stage.AFTER_ISSUE_BEFORE_EFFECT
            real = reproduce(stage, target=tools[-1].name, cancel_tool_with_reply=True)
            session.phase = Phase[real.phase]
            event_labels = list(real.events)
            events = None
        else:
            calls = [ToolCall("advance_phase", {"target": t}) for t in tools]
            events = session.handle_turn(Turn(caller, interrupted=interrupted, answers_a_discovery_question=answers), calls)
            event_labels = [e.kind + (":" + e.detail if e.detail else "") for e in events]

        exchanges.append(
            Exchange(
                index=len(exchanges),
                kind=kind,
                phase_at_start=phase.name,
                agent_full=text,
                tool_calls=[t.name for t in tools],
                caller=caller,
                interrupted=interrupted,
                cut_words=cut_words,
                answers_discovery=answers,
                interruption_type=interruption_type,
            )
        )
        trace.append(
            TraceEntry(
                index=len(trace),
                phase_before=phase.name,
                phase_after=session.phase.name,
                facts_before=_facts(facts),
                facts_after=_facts(session.facts),
                interrupted=interrupted,
                tool_calls=[t.name for t in tools],
                events=event_labels,
            )
        )
        if interrupted:
            retry_kind, recovering, deferred_move = kind, True, False
        elif recovering and variant == "deferred" and arm != "bad_recovery":
            # Keep the fresh-start rendering for the phase move that follows.
            recovering, deferred_move = False, True
        else:
            retry_kind, recovering, deferred_move = None, False, False
        if expect == "goodbye":
            break

    if injected_at is None:
        raise RuntimeError(f"no injection point reached for {plan.transition} (index {index})")

    return {
        "id": session_id(seed, arm, index, variant, "" if state_source == "model" else state_source),
        "arm": arm,
        "index": index,
        "seed": seed,
        "variant": variant or "main",
        "state_source": state_source,
        "transition": plan.transition,
        "interruption_type": plan.interruption_type,
        "injection_index": injected_at,
        "cast": {
            "agent": plan.agent,
            "caller": plan.caller,
            "company": plan.company,
            "caller_company": plan.caller_company,
            "vertical": plan.vertical["name"],
        },
        "system_prompt": r.system_prompt(),
        "recovery_criteria": list(bank.RECOVERY_CRITERIA[plan.interruption_type]),
        "exchanges": [asdict(e) for e in exchanges],
        "trace": [asdict(t) for t in trace],
    }


def generate(
    seed: int, n: int, arms: tuple[str, ...] = ARMS, variant: str = "", state_source: str = "model"
) -> list[dict[str, Any]]:
    return [build_session(seed, arm, i, variant, state_source) for arm in arms for i in range(n)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--seed", type=int, default=20260902)
    p.add_argument("--n", type=int, default=200, help="sessions per arm")
    p.add_argument("--out", type=Path, default=Path("results/judge_blindness/sessions.jsonl"))
    p.add_argument("--arms", nargs="+", default=list(ARMS), choices=ARMS)
    p.add_argument("--variant", default="", choices=VARIANTS)
    p.add_argument("--state-source", default="model", choices=STATE_SOURCES)
    args = p.parse_args(argv)
    sessions = generate(args.seed, args.n, tuple(args.arms), args.variant, args.state_source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as fh:
        for s in sessions:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    by_arm: dict[str, int] = {}
    for s in sessions:
        by_arm[s["arm"]] = by_arm.get(s["arm"], 0) + 1
    print(f"wrote {len(sessions)} sessions to {args.out} (seed {args.seed}): {by_arm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
