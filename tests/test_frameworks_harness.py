"""Tests for the cross-framework harness, run against the framework-free text agent.

These need nothing but the standard library, so CI checks the harness's
determinism and classification without any voice framework installed. The
runs that drive real runtimes are behind the `frameworks` marker and are
executed by `experiments/frameworks/run_matrix.py`, not by this file.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMEWORKS = os.path.abspath(os.path.join(HERE, "..", "experiments", "frameworks"))
sys.path.insert(0, FRAMEWORKS)

from harness import (  # noqa: E402
    INITIAL_PHASE,
    TARGET_PHASE,
    NotMeasured,
    RunRecord,
    classify_run,
    make_schedule,
    render_matrix_md,
    render_matrix_tex,
    summarise,
)
from harness.protocol import CONFIG_BY_NAME, CONFIGS  # noqa: E402
from text_agent.agent import POLICIES, run_one  # noqa: E402

SCALE = 0.02  # every second of scenario time becomes 20 ms of wall time
SEED = 7


def _run(config_name: str, policy: str, n: int = 8, seed: int = SEED) -> list[RunRecord]:
    config = CONFIG_BY_NAME[config_name]

    async def go() -> list[RunRecord]:
        out = []
        for sched in make_schedule(config, n=n, seed=seed):
            out.append(await run_one(config, sched, policy=policy, time_scale=SCALE))
        return out

    return asyncio.run(go())


# -- schedule -------------------------------------------------------------------


def test_schedule_is_a_pure_function_of_config_and_seed() -> None:
    cfg = CONFIG_BY_NAME["sync-tool"]
    a = make_schedule(cfg, n=20, seed=123)
    b = make_schedule(cfg, n=20, seed=123)
    c = make_schedule(cfg, n=20, seed=124)
    assert a == b
    assert a != c
    assert [s.run_index for s in a] == list(range(20))


def test_schedule_jitter_stays_inside_the_declared_window() -> None:
    for cfg in CONFIGS:
        for s in make_schedule(cfg, n=200, seed=1):
            assert abs(s.barge_in_offset_s - cfg.barge_in_offset_s) <= cfg.barge_in_jitter_s + 1e-9
            assert s.tool_delay_s == cfg.tool_delay_s


# -- classification -------------------------------------------------------------


def _rec(phase: str) -> RunRecord:
    return RunRecord(
        runtime="x",
        runtime_version="y",
        config="sync-tool",
        run_index=0,
        seed=1,
        barge_in_offset_s=1.0,
        tool_delay_s=0.0,
        phase_final=phase,
        tool_emitted=True,
        tool_started=True,
        tool_finished=True,
        tool_cancelled=False,
        speech_interrupted=True,
    )


def test_classification_reads_the_final_phase_only() -> None:
    assert classify_run(_rec(TARGET_PHASE)) == "committed"
    assert classify_run(_rec(INITIAL_PHASE)) == "cancelled"
    assert classify_run(_rec("PITCH")).startswith("unexpected")


def test_summary_verdicts() -> None:
    all_c = summarise([_rec(TARGET_PHASE) for _ in range(5)])
    assert all_c.verdict == "committed" and all_c.rate == 1.0
    all_x = summarise([_rec(INITIAL_PHASE) for _ in range(5)])
    assert all_x.verdict == "cancelled" and all_x.rate == 0.0
    mixed = summarise([_rec(TARGET_PHASE), _rec(INITIAL_PHASE)])
    assert mixed.verdict == "race-dependent" and mixed.rate == 0.5
    with pytest.raises(ValueError):
        summarise([])


# -- the framework-free agent, every policy, every configuration -----------------


EXPECTED = {
    # policy: {config: verdict}
    "speculative": {
        "sync-tool": "committed",
        "inflight-tool": "committed",
        "late-tool": "cancelled",
        "disallow-interruptions": "committed",
        "handoff-tool": "committed",
    },
    "cancel-tool": {
        "sync-tool": "committed",
        "inflight-tool": "cancelled",
        "late-tool": "cancelled",
        "disallow-interruptions": "committed",
        "handoff-tool": "committed",
    },
    "staged": {
        "sync-tool": "cancelled",
        "inflight-tool": "cancelled",
        "late-tool": "cancelled",
        "disallow-interruptions": "committed",
        "handoff-tool": "cancelled",
    },
}


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("config_name", [c.name for c in CONFIGS])
def test_text_agent_outcome_is_deterministic(policy: str, config_name: str) -> None:
    recs = _run(config_name, policy)
    summary = summarise(recs)
    assert summary.verdict == EXPECTED[policy][config_name], [r.to_json() for r in recs]
    assert summary.n == len(recs) == 8
    assert summary.unexpected == 0


def test_speculative_sync_tool_commits_although_the_turn_was_stopped() -> None:
    recs = _run("sync-tool", "speculative")
    for r in recs:
        assert r.speech_interrupted, "the stop button must have been honoured"
        assert r.tool_finished and not r.tool_cancelled
        assert r.phase_final == TARGET_PHASE
        # the reply was cut well short of its full length
        assert r.playback_seconds is not None and r.playback_seconds < 3.0


def test_speculative_late_tool_never_emits_the_call() -> None:
    for r in _run("late-tool", "speculative"):
        assert r.speech_interrupted
        assert not r.tool_emitted and not r.tool_started
        assert r.phase_final == INITIAL_PHASE


def test_cancel_tool_policy_makes_the_outcome_a_timing_question() -> None:
    sync = _run("sync-tool", "cancel-tool")
    inflight = _run("inflight-tool", "cancel-tool")
    assert all(r.tool_finished and not r.tool_cancelled for r in sync)
    assert all(r.tool_cancelled and not r.tool_finished for r in inflight)


def test_disallow_interruptions_suppresses_the_stop_and_plays_the_whole_reply() -> None:
    recs = _run("disallow-interruptions", "speculative")
    for r in recs:
        assert not r.speech_interrupted
        assert "interruptions disallowed" in r.notes
        assert r.playback_seconds is not None and r.playback_seconds >= 7.9
        assert r.phase_final == TARGET_PHASE


def test_staged_policy_holds_the_line_on_every_timing() -> None:
    for cfg in ("sync-tool", "inflight-tool", "late-tool", "handoff-tool"):
        recs = _run(cfg, "staged")
        assert all(r.phase_final == INITIAL_PHASE for r in recs), cfg
        if cfg == "handoff-tool":
            assert all(r.handoff_applied is False for r in recs)


def test_same_seed_reproduces_the_same_records() -> None:
    a = [json.loads(r.to_json()) for r in _run("sync-tool", "speculative", n=5)]
    b = [json.loads(r.to_json()) for r in _run("sync-tool", "speculative", n=5)]
    volatile = {
        "wall_time_s",
        "t_tool_started_s",
        "t_tool_finished_s",
        "t_interrupt_s",
        "playback_seconds",  # tokens spoken before the stop: a wall-clock measurement
    }
    for x, y in zip(a, b):
        assert {k: v for k, v in x.items() if k not in volatile} == {
            k: v for k, v in y.items() if k not in volatile
        }


# -- reports --------------------------------------------------------------------


def test_reports_render_measured_and_not_measured_cells() -> None:
    cells = [
        summarise([_rec(TARGET_PHASE) for _ in range(3)]),
        NotMeasured("y-runtime", "0.1", "sync-tool", "needs an API key"),
    ]
    md = render_matrix_md(cells, seed=1, n=3, commit="abc123")
    tex = render_matrix_tex(cells, seed=1, n=3, commit="abc123")
    assert "committed 3/3" in md and "not measured" in md and "needs an API key" in md
    assert "\\begin{tabular}" in tex and "not measured" in tex
    assert "—" not in md and "—" not in tex  # no em-dashes anywhere


# -- the CLI, as run_matrix.py invokes it ------------------------------------------


def test_text_agent_cli_writes_records_and_a_summary(tmp_path) -> None:
    out = tmp_path / "r.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            os.path.join(FRAMEWORKS, "text_agent", "run.py"),
            "--policy",
            "speculative",
            "--config",
            "sync-tool",
            "--n",
            "4",
            "--seed",
            "3",
            "--time-scale",
            str(SCALE),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["verdict"] == "committed" and summary["n"] == 4 and summary["seed"] == 3
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 4
    assert all(json.loads(line)["outcome"] == "committed" for line in lines)


@pytest.mark.frameworks
def test_livekit_venvs_are_only_exercised_by_run_matrix() -> None:
    """Placeholder behind the marker: the real-runtime cells take minutes and need
    the per-runtime venvs, so they run through run_matrix.py, not pytest."""
    assert os.path.exists(os.path.join(FRAMEWORKS, "run_matrix.py"))
