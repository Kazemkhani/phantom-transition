"""The scenario, the seeded schedule, the per-run record and the classification rule.

Vocabulary used across every runtime:

committed
    After the interrupted turn has fully resolved, the session phase is the
    tool's target. The side effect survived the interruption.
cancelled
    After the turn has resolved, the session phase is the initial phase. Either
    the tool never ran (the runtime cancelled the generation before the call
    was emitted) or the runtime undid its effect.
race-dependent
    Across N seeded runs of one configuration, some runs committed and some
    cancelled. The rate is reported; the mechanism is named in the notes.

A "run" is one session, one user turn, one scripted reply carrying one tool
call, one barge-in. Nothing is reused between runs.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from typing import Iterable

INITIAL_PHASE = "GREETING"
TARGET_PHASE = "DISCOVERY"
TOOL_NAME = "advance_phase"
USER_UTTERANCE = "hello there"
BARGE_IN_UTTERANCE = "wait wait stop stop please"

# A reply long enough that every barge-in offset in the matrix lands inside it.
# Frameworks that synthesise text into audio are told the audio lasts
# REPLY_AUDIO_SECONDS; text-only runtimes stream it at REPLY_TOKEN_INTERVAL.
REPLY_TEXT = (
    "Thank you, I am moving us along to the discovery questions now. "
    "Before we go any further I want to understand your situation properly, "
    "so I am going to ask you a few short questions about what you are looking for, "
    "what your timeline looks like, and who else is involved in the decision. "
    "Take your time with each one and interrupt me whenever you need to."
)
REPLY_AUDIO_SECONDS = 8.0
REPLY_TOKEN_INTERVAL = 0.05


@dataclass(frozen=True)
class Config:
    """One column of the matrix.

    tool_delay_s
        How long the tool body awaits before it mutates state. 0.0 is a
        synchronous mutation with no await point. A positive value puts the
        mutation after an await so the tool is in flight at the barge-in.
    tool_emit_offset_s
        When, relative to the first LLM chunk, the tool call is emitted.
    barge_in_offset_s
        Nominal instant of the barge-in relative to the first agent audio
        frame (or first streamed token for text runtimes).
    barge_in_jitter_s
        Half-width of the uniform jitter the seed draws around the nominal
        offset, so N runs sample a window rather than repeat one instant.
    disallow_interruptions
        Whether the tool invokes the runtime's per-tool barge-in opt-out
        (LiveKit: RunContext.disallow_interruptions()) before mutating.
    handoff
        Whether the tool, after mutating state, also returns the runtime's
        agent-handoff primitive (LiveKit: returning an Agent). This is the
        shape the production transition tools take. Runtimes without a
        handoff primitive report this configuration as not measured.
    """

    name: str
    description: str
    tool_delay_s: float = 0.0
    tool_emit_offset_s: float = 0.0
    barge_in_offset_s: float = 1.0
    barge_in_jitter_s: float = 0.0
    disallow_interruptions: bool = False
    handoff: bool = False


# The configurations every runtime is asked to run. A runtime that cannot run
# one reports it as NotMeasured with the reason; it never guesses.
CONFIGS: tuple[Config, ...] = (
    Config(
        name="sync-tool",
        description=(
            "tool mutates state synchronously as soon as the call is emitted; "
            "barge-in 1.0 s into the reply"
        ),
        tool_delay_s=0.0,
        tool_emit_offset_s=0.0,
        barge_in_offset_s=1.0,
        barge_in_jitter_s=0.2,
    ),
    Config(
        name="inflight-tool",
        description=(
            "tool awaits 3.0 s before mutating, so it is still executing when "
            "the barge-in lands 1.0 s into the reply"
        ),
        tool_delay_s=3.0,
        tool_emit_offset_s=0.0,
        barge_in_offset_s=1.0,
        barge_in_jitter_s=0.2,
    ),
    Config(
        name="late-tool",
        description=(
            "tool call is emitted 4.0 s into the reply, after the barge-in at 1.0 s; "
            "measures whether cancellation reaches a call not yet emitted"
        ),
        tool_delay_s=0.0,
        tool_emit_offset_s=4.0,
        barge_in_offset_s=1.0,
        barge_in_jitter_s=0.2,
    ),
    Config(
        name="disallow-interruptions",
        description=(
            "as sync-tool, but the tool invokes the runtime's per-tool barge-in "
            "opt-out before mutating; measures what the opt-out costs"
        ),
        tool_delay_s=0.0,
        tool_emit_offset_s=0.0,
        barge_in_offset_s=1.0,
        barge_in_jitter_s=0.2,
        disallow_interruptions=True,
    ),
    Config(
        name="handoff-tool",
        description=(
            "as sync-tool, but the tool also returns the runtime's agent-handoff "
            "primitive after mutating, the shape of the production transition tools; "
            "measures whether the mutation and the handoff share a fate"
        ),
        tool_delay_s=0.0,
        tool_emit_offset_s=0.0,
        barge_in_offset_s=1.0,
        barge_in_jitter_s=0.2,
        handoff=True,
    ),
)

CONFIG_BY_NAME = {c.name: c for c in CONFIGS}


@dataclass(frozen=True)
class Schedule:
    """The seeded timing of one run."""

    run_index: int
    seed: int
    barge_in_offset_s: float
    tool_delay_s: float


def make_schedule(config: Config, *, n: int, seed: int) -> list[Schedule]:
    """Draw N per-run timings from one seeded generator.

    The generator is seeded once per (config, seed) so the schedule is a pure
    function of its arguments and a reviewer can regenerate it exactly.
    """
    rng = random.Random(f"{config.name}:{seed}")
    out: list[Schedule] = []
    for i in range(n):
        jitter = rng.uniform(-config.barge_in_jitter_s, config.barge_in_jitter_s)
        out.append(
            Schedule(
                run_index=i,
                seed=seed,
                barge_in_offset_s=round(config.barge_in_offset_s + jitter, 4),
                tool_delay_s=config.tool_delay_s,
            )
        )
    return out


@dataclass
class RunRecord:
    """Everything one run observed. Written as one JSON line."""

    runtime: str
    runtime_version: str
    config: str
    run_index: int
    seed: int
    barge_in_offset_s: float
    tool_delay_s: float
    phase_final: str
    tool_emitted: bool
    tool_started: bool
    tool_finished: bool
    tool_cancelled: bool
    speech_interrupted: bool
    tool_record_in_context: bool | None = None
    tools_executed_event: bool | None = None
    handoff_applied: bool | None = None
    playback_seconds: float | None = None
    t_tool_started_s: float | None = None
    t_tool_finished_s: float | None = None
    t_interrupt_s: float | None = None
    wall_time_s: float | None = None
    notes: str = ""

    @property
    def outcome(self) -> str:
        return classify_run(self)

    def to_json(self) -> str:
        d = asdict(self)
        d["outcome"] = self.outcome
        return json.dumps(d, sort_keys=True)


def classify_run(rec: RunRecord) -> str:
    """One run is committed if the mutation survived, cancelled otherwise.

    The rule reads the final phase only. Whether the tool ran, whether its
    record was kept, whether the speech was cut are all recorded but do not
    enter the classification: the paper's question is about state.
    """
    if rec.phase_final == TARGET_PHASE:
        return "committed"
    if rec.phase_final == INITIAL_PHASE:
        return "cancelled"
    return f"unexpected:{rec.phase_final}"


@dataclass
class Summary:
    runtime: str
    runtime_version: str
    config: str
    n: int
    seed: int
    committed: int
    cancelled: int
    unexpected: int
    barge_in_suppressed: int
    tool_finished: int
    tool_record_kept: int | None
    handoff_applied: int | None = None
    mechanism: str = ""

    @property
    def verdict(self) -> str:
        if self.n == 0:
            return "not measured"
        if self.unexpected:
            return "unexpected"
        if self.committed == self.n:
            return "committed"
        if self.cancelled == self.n:
            return "cancelled"
        return "race-dependent"

    @property
    def rate(self) -> float:
        return self.committed / self.n if self.n else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict
        d["rate"] = self.rate
        return d


@dataclass(frozen=True)
class NotMeasured:
    runtime: str
    runtime_version: str
    config: str
    reason: str

    def to_dict(self) -> dict:
        return {**asdict(self), "verdict": "not measured"}


def summarise(records: Iterable[RunRecord], *, mechanism: str = "") -> Summary:
    recs = list(records)
    if not recs:
        raise ValueError("cannot summarise zero runs; report NotMeasured instead")
    first = recs[0]
    outcomes = [r.outcome for r in recs]
    kept = [r.tool_record_in_context for r in recs]
    handoffs = [r.handoff_applied for r in recs]
    return Summary(
        runtime=first.runtime,
        runtime_version=first.runtime_version,
        config=first.config,
        n=len(recs),
        seed=first.seed,
        committed=outcomes.count("committed"),
        cancelled=outcomes.count("cancelled"),
        unexpected=sum(1 for o in outcomes if o.startswith("unexpected")),
        barge_in_suppressed=sum(1 for r in recs if not r.speech_interrupted),
        tool_finished=sum(1 for r in recs if r.tool_finished),
        tool_record_kept=(sum(1 for k in kept if k) if all(k is not None for k in kept) else None),
        handoff_applied=(
            sum(1 for h in handoffs if h) if all(h is not None for h in handoffs) else None
        ),
        mechanism=mechanism,
    )


def load_records(path: str) -> list[RunRecord]:
    out: list[RunRecord] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            d.pop("outcome", None)
            out.append(RunRecord(**d))
    return out
