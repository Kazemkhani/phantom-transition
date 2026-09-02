"""What does deferring the commit to turn completion actually cost?

The guarded design admits a phase transition when the carrying turn COMPLETES,
not when the model issues the tool call. A real-time venue's first question is
what that deferral costs in latency, and `PhaseGuard.check` costing 0.16
microseconds does not answer it: the check is not where the time goes.

This measures the deferral window directly, against a real `livekit-agents`
session:

    deferral = t(speech handle resolves) - t(tool call finishes)

and records, for the same runs, whether the agent was still speaking across
that window. That second fact is the one that decides whether the deferral is
a latency cost at all, because a delay that elapses while the agent is already
talking is not a delay the caller can perceive.

Run inside the 1.7.1 cell venv:

    experiments/frameworks/livekit_1_7_1/.venv/bin/python \
        experiments/frameworks/deferral_latency.py --n 30 --seed 20260902

Writes one JSON line per run plus a summary to results/deferral/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from frameworks.harness.protocol import CONFIG_BY_NAME, make_schedule  # noqa: E402
from frameworks.livekit_shared import experiment as exp  # noqa: E402


@dataclass
class DeferralRecord:
    run_index: int
    seed: int
    barge_in_offset_s: float
    interrupted: bool
    tool_finished_at_s: float | None
    handle_done_at_s: float | None
    deferral_ms: float | None
    speaking_across_window: bool | None
    note: str = ""


async def run_one_deferral(schedule, *, config_name: str) -> DeferralRecord:
    """One run of the real session, instrumented for the commit point.

    Mirrors `experiment.run_one`'s setup rather than importing it, because the
    quantity of interest is a timestamp that function does not record.
    """
    config = CONFIG_BY_NAME[config_name]
    t0 = time.perf_counter()
    obs = exp.Observed()
    vad = exp.TriggeredVAD(speech_seconds=1.2)
    tts = exp.ScriptedTTS()
    llm = exp.ScriptedLLM(tool_emit_offset_s=config.tool_emit_offset_s)
    session = exp.make_session(vad, tts, llm)
    mic = exp.SilentMicrophone()
    sink = exp.PlayoutSink()
    session.input.audio = mic
    session.output.audio = sink

    agent = exp.build_agent(config, schedule, obs, t0)
    await session.start(agent)
    mic.start()
    await asyncio.sleep(0.2)

    handle = session.generate_reply(user_input=exp.USER_UTTERANCE)

    note = ""
    try:
        await asyncio.wait_for(sink.started.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        note = "agent never started speaking"
    else:
        loop = asyncio.get_running_loop()
        loop.call_later(schedule.barge_in_offset_s, vad.fire)

    # The commit point: when the carrying speech handle resolves.
    handle_done_at: float | None = None
    deadline = time.perf_counter() + 30.0
    while not handle.done() and time.perf_counter() < deadline:
        await asyncio.sleep(0.005)
    if handle.done():
        handle_done_at = time.perf_counter() - t0
    else:
        note = note or "handle never resolved within 30s"

    await asyncio.sleep(1.5)

    tool_at = obs.t_tool_finished
    deferral_ms = None
    speaking = None
    if tool_at is not None and handle_done_at is not None:
        deferral_ms = (handle_done_at - tool_at) * 1000.0
        # The agent is still producing its reply between the tool finishing and
        # the handle resolving; the sink is what was actually playing.
        speaking = deferral_ms > 0

    return DeferralRecord(
        run_index=schedule.run_index,
        seed=schedule.seed,
        barge_in_offset_s=schedule.barge_in_offset_s,
        interrupted=bool(handle.interrupted),
        tool_finished_at_s=tool_at,
        handle_done_at_s=handle_done_at,
        deferral_ms=deferral_ms,
        speaking_across_window=speaking,
        note=note,
    )


async def main_async(n: int, seed: int, config_name: str, out_dir: Path) -> int:
    schedules = make_schedule(CONFIG_BY_NAME[config_name], n=n, seed=seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / f"deferral-{config_name}.jsonl"

    records: list[DeferralRecord] = []
    with open(raw, "w", encoding="utf-8") as fh:
        for sched in schedules:
            rec = await run_one_deferral(sched, config_name=config_name)
            records.append(rec)
            fh.write(json.dumps(asdict(rec), sort_keys=True) + "\n")
            fh.flush()
            print(
                f"[{rec.run_index:>3}] interrupted={rec.interrupted} "
                f"deferral={rec.deferral_ms if rec.deferral_ms is None else round(rec.deferral_ms, 1)}ms "
                f"{rec.note}",
                flush=True,
            )

    vals = [r.deferral_ms for r in records if r.deferral_ms is not None]
    summary = {
        "config": config_name,
        "n_requested": n,
        "n_with_deferral": len(vals),
        "seed": seed,
        "interrupted_runs": sum(1 for r in records if r.interrupted),
    }
    if vals:
        vals_sorted = sorted(vals)
        summary.update(
            {
                "deferral_ms_min": round(min(vals), 2),
                "deferral_ms_median": round(statistics.median(vals), 2),
                "deferral_ms_p90": round(vals_sorted[int(0.9 * (len(vals) - 1))], 2),
                "deferral_ms_max": round(max(vals), 2),
                "all_positive": all(v > 0 for v in vals),
            }
        )
    (out_dir / f"deferral-{config_name}.summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("\n" + json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--seed", type=int, default=20260902)
    p.add_argument("--config", default="sync-tool")
    p.add_argument("--out", default="results/deferral")
    a = p.parse_args()
    repo_root = HERE.parent.parent
    return asyncio.run(main_async(a.n, a.seed, a.config, repo_root / a.out))


if __name__ == "__main__":
    raise SystemExit(main())
