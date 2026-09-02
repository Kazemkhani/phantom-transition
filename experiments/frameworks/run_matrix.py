"""Run every (runtime, configuration) cell and write the matrix.

    python experiments/frameworks/run_matrix.py --n 50 --seed 20260902

Outputs (all under results/frameworks/):

    raw/<runtime>-<config>.jsonl          one JSON line per run
    raw/<runtime>-<config>.summary.json   the cell summary and the exact command
    matrix.md, matrix.tex                 the table
    MANIFEST.md                           commit, seed, N, commands, pip freeze per venv

Each runtime lives in its own venv under experiments/frameworks/<runtime>/.venv.
A runtime whose venv is absent, or whose cell fails, is written as "not measured"
with the reason. The runner never fills a cell it did not run.

    --runtimes a,b     restrict to some runtimes (default: all)
    --configs a,b      restrict to some configurations (default: all)
    --assemble-only    rebuild the matrix from the summaries already on disk
    --parallel K       run up to K runtimes at once (cells within a runtime are serial)
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
RESULTS = os.path.join(REPO, "results", "frameworks")
RAW = os.path.join(RESULTS, "raw")
sys.path.insert(0, HERE)

from harness import (  # noqa: E402
    NotMeasured,
    Summary,
    render_manifest,
    render_matrix_md,
    render_matrix_tex,
)
from harness.protocol import CONFIGS  # noqa: E402


@dataclass(frozen=True)
class Runtime:
    key: str
    label: str
    python: str  # interpreter to use; relative to HERE unless absolute
    script: str  # relative to HERE
    extra_args: tuple[str, ...] = ()
    version_hint: str = ""
    unsupported: tuple[str, ...] = ()  # configs this runtime cannot express
    unsupported_reason: str = ""
    mechanism: dict[str, str] | None = None


LK_171_MECH = (
    "agent_activity.py:3610-3630 (_pipeline_reply_task_impl): on interruption "
    "`await utils.aio.cancel_and_wait(exe_task)` (line 3611) cancels the tool dispatcher "
    "task, whose `except asyncio.CancelledError` at generation.py:968-978 awaits the running "
    "tool tasks to completion instead of cancelling them; every finished tool's FunctionCall "
    "and FunctionCallOutput are then inserted into the chat context and "
    "`function_tools_executed` is emitted (lines 3621-3630; comment at 3613: commit results "
    "of tools that finished despite the interruption, #3702). generation.py:1077-1088 "
    "`_interrupted_tool_output` rewrites only an agent-handoff output to an error and the "
    "handoff is never applied; a state mutation inside the tool body stands. A call the LLM "
    "had not yet emitted is never made because the LLM task is cancelled with the other "
    "generation tasks at agent_activity.py:3561. disallow_interruptions() "
    "(events.py:88-97) sets SpeechHandle.allow_interruptions=False, which "
    "_interrupt_by_audio_activity checks at agent_activity.py:2145 before calling "
    "interrupt() at 2176"
)
LK_1310_MECH = (
    "agent_activity.py:2001-2039 (_pipeline_reply_task): on interruption the reply task "
    "cancels the generation tasks including the LLM stream (line 2002), clears the audio "
    "buffer, records the interrupted assistant message, then "
    "`await utils.aio.cancel_and_wait(exe_task)` (2038) and `return` (2039); "
    "generation.py:641-651 catches the CancelledError and awaits the running tool tasks to "
    "completion, so the tool body runs and its mutation stands, but the return happens "
    "before the `function_tools_executed` emit (2115), the handoff `update_agent` (2119) and "
    "the FunctionCall/FunctionCallOutput insertion (2154-2155), so no record and no event "
    "survive and a returned Agent is never applied. disallow_interruptions() "
    "(events.py:63-72) sets SpeechHandle.allow_interruptions=False, which "
    "_interrupt_by_audio_activity checks at agent_activity.py:1200 before calling "
    "interrupt() at 1216"
)


def runtimes() -> list[Runtime]:
    # The framework-free asyncio reproduction is measured separately
    # (results/core-v2/asyncio-interleaving.txt, with a six-row interleaving
    # survey); the text agent under experiments/frameworks/text_agent/ is the
    # harness's CI substrate, exercised by tests/test_frameworks_harness.py,
    # and is deliberately not a matrix row so the evidence is not duplicated.
    rts: list[Runtime] = []
    rts.append(
        Runtime(
            key="livekit-1.7.1",
            label="LiveKit Agents (Python) 1.7.1",
            python="livekit_1_7_1/.venv/bin/python",
            script="livekit_shared/run.py",
            version_hint="1.7.1",
            mechanism={c.name: LK_171_MECH for c in CONFIGS},
        )
    )
    rts.append(
        Runtime(
            key="livekit-1.3.10",
            label="LiveKit Agents (Python) 1.3.10",
            python="livekit_1_3_10/.venv/bin/python",
            script="livekit_shared/run.py",
            version_hint="1.3.10",
            mechanism={c.name: LK_1310_MECH for c in CONFIGS},
        )
    )
    rts.append(
        Runtime(
            key="pipecat",
            label="Pipecat",
            python="pipecat/.venv/bin/python",
            script="pipecat/run.py",
            unsupported=("handoff-tool", "disallow-interruptions"),
            unsupported_reason=(
                "Pipecat has no agent-handoff primitive and no per-tool barge-in opt-out; "
                "the cell has no faithful equivalent"
            ),
        )
    )
    rts.append(
        Runtime(
            key="openai-agents",
            label="OpenAI Agents SDK (Python)",
            python="openai_agents/.venv/bin/python",
            script="openai_agents/run.py",
            unsupported=("disallow-interruptions",),
            unsupported_reason=(
                "the SDK has no per-tool opt-out from cancellation; the cell has no "
                "faithful equivalent"
            ),
        )
    )
    return rts


def _abs(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(HERE, p)


def run_cell(rt: Runtime, config_name: str, *, n: int, seed: int) -> dict:
    raw_path = os.path.join(RAW, f"{rt.key}-{config_name}.jsonl")
    summary_path = os.path.join(RAW, f"{rt.key}-{config_name}.summary.json")
    python = _abs(rt.python)
    script = _abs(rt.script)
    if config_name in rt.unsupported:
        cell = NotMeasured(rt.label, rt.version_hint, config_name, rt.unsupported_reason)
        out = {**cell.to_dict(), "command": None}
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, sort_keys=True)
        return out
    if not os.path.exists(python):
        cell = NotMeasured(
            rt.label, rt.version_hint, config_name, f"interpreter not present: {python}"
        )
        out = {**cell.to_dict(), "command": None}
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, sort_keys=True)
        return out
    cmd = [
        python,
        script,
        *rt.extra_args,
        "--config",
        config_name,
        "--n",
        str(n),
        "--seed",
        str(seed),
        "--out",
        raw_path,
    ]
    printable = " ".join(os.path.relpath(c, REPO) if c.startswith(REPO) else c for c in cmd)
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    finished = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if proc.returncode != 0 or not proc.stdout.strip():
        reason = (
            f"cell process exited {proc.returncode}; last stderr lines: "
            + " | ".join(proc.stderr.strip().splitlines()[-5:])
        )
        cell = NotMeasured(rt.label, rt.version_hint, config_name, reason)
        out = {**cell.to_dict(), "command": printable, "started": started, "finished": finished}
    else:
        d = json.loads(proc.stdout.strip().splitlines()[-1])
        d["runtime"] = rt.label.rsplit(" ", 1)[0] if rt.version_hint else rt.label
        if rt.version_hint:
            d["runtime_version"] = d.get("runtime_version") or rt.version_hint
        if rt.mechanism and config_name in rt.mechanism and not d.get("mechanism"):
            d["mechanism"] = rt.mechanism[config_name]
        out = {**d, "command": printable, "started": started, "finished": finished}
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    return out


def run_runtime(rt: Runtime, config_names: list[str], *, n: int, seed: int) -> list[dict]:
    out = []
    for name in config_names:
        print(f"[{rt.key}] {name} ...", flush=True)
        cell = run_cell(rt, name, n=n, seed=seed)
        print(f"[{rt.key}] {name}: {cell.get('verdict')}", flush=True)
        out.append(cell)
    return out


def _cell_from_dict(d: dict) -> Summary | NotMeasured:
    if d.get("verdict") == "not measured":
        return NotMeasured(d["runtime"], d["runtime_version"], d["config"], d["reason"])
    keys = {
        "runtime",
        "runtime_version",
        "config",
        "n",
        "seed",
        "committed",
        "cancelled",
        "unexpected",
        "barge_in_suppressed",
        "tool_finished",
        "tool_record_kept",
        "handoff_applied",
        "mechanism",
    }
    return Summary(**{k: v for k, v in d.items() if k in keys})


def assemble(*, seed: int, n: int, commit: str, started: str, finished: str) -> None:
    # keep the registry order, then config order
    order = {rt.key: i for i, rt in enumerate(runtimes())}
    cfg_order = {c.name: i for i, c in enumerate(CONFIGS)}

    def _key(d: dict) -> tuple[int, int]:
        fn_key = next((k for k in order if d.get("_file", "").startswith(k + "-")), None)
        return (order.get(fn_key, 99), cfg_order.get(d["config"], 99))

    # attach file names for ordering
    named = []
    for fn in sorted(os.listdir(RAW)):
        if fn.endswith(".summary.json"):
            with open(os.path.join(RAW, fn), encoding="utf-8") as fh:
                d = json.load(fh)
            d["_file"] = fn
            named.append(d)
    named.sort(key=_key)
    cells = [_cell_from_dict(d) for d in named]
    commands = [d["command"] for d in named if d.get("command")]

    environments = []
    for rt in runtimes():
        python = _abs(rt.python)
        if not os.path.exists(python):
            continue
        freeze = subprocess.run(
            [python, "-m", "pip", "freeze"], capture_output=True, text=True
        ).stdout
        if not freeze.strip():
            freeze = subprocess.run(
                ["uv", "pip", "freeze", "--python", python], capture_output=True, text=True
            ).stdout
        pyver = subprocess.run([python, "--version"], capture_output=True, text=True).stdout.strip()
        environments.append((rt.label, f"{python} ({pyver})", freeze))
    # de-duplicate identical interpreters (the text-agent policies share one)
    seen = set()
    envs = []
    for label, py, fr in environments:
        if py in seen:
            continue
        seen.add(py)
        envs.append((label, py, fr))

    os.makedirs(RESULTS, exist_ok=True)
    notes = [
        "The framework-free case (a plain asyncio agent with a stop button) is measured "
        "separately in `results/core-v2/asyncio-interleaving.txt`: cancelling a task does "
        "not cancel the tasks it spawned, and a barge-in after the tool's effect has landed "
        "is a phantom transition even when the tool task is cancelled."
    ]
    with open(os.path.join(RESULTS, "matrix.md"), "w", encoding="utf-8") as fh:
        fh.write(render_matrix_md(cells, seed=seed, n=n, commit=commit, extra_notes=notes))
    with open(os.path.join(RESULTS, "matrix.tex"), "w", encoding="utf-8") as fh:
        fh.write(render_matrix_tex(cells, seed=seed, n=n, commit=commit))
    machine = f"{platform.platform()} {platform.machine()}, {platform.python_implementation()}"
    with open(os.path.join(RESULTS, "MANIFEST.md"), "w", encoding="utf-8") as fh:
        fh.write(
            render_manifest(
                commit=commit,
                seed=seed,
                n=n,
                commands=commands,
                environments=envs,
                machine=machine,
                started=started,
                finished=finished,
            )
        )
    print(open(os.path.join(RESULTS, "matrix.md"), encoding="utf-8").read())


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, cwd=REPO
        ).stdout.strip()
    except OSError:
        return "unknown"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=20260902)
    p.add_argument("--runtimes", default="")
    p.add_argument("--configs", default="")
    p.add_argument("--parallel", type=int, default=3)
    p.add_argument("--assemble-only", action="store_true")
    args = p.parse_args()

    os.makedirs(RAW, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    commit = git_commit()
    if not args.assemble_only:
        wanted = set(args.runtimes.split(",")) if args.runtimes else None
        cfgs = args.configs.split(",") if args.configs else [c.name for c in CONFIGS]
        selected = [rt for rt in runtimes() if wanted is None or rt.key in wanted]
        with cf.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
            futs = [ex.submit(run_runtime, rt, cfgs, n=args.n, seed=args.seed) for rt in selected]
            for f in futs:
                f.result()
    finished = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    assemble(seed=args.seed, n=args.n, commit=commit, started=started, finished=finished)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
