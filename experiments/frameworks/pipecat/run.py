"""CLI: run N seeded runs of one configuration against the installed Pipecat.

    pipecat/.venv/bin/python experiments/frameworks/pipecat/run.py \
        --config sync-tool --n 50 --seed 20260902 --out results.jsonl

`--optout` forces cancel_on_interruption=False on the tool for any
configuration, for the side measurement of what Pipecat's per-tool opt-out
changes at in-flight timing.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from harness import make_schedule, summarise  # noqa: E402
from harness.protocol import CONFIG_BY_NAME  # noqa: E402


async def _main(args: argparse.Namespace) -> int:
    from pipecat.utils.asyncio.task_manager import BaseTaskManager  # noqa: F401

    sys.path.insert(0, HERE)
    from experiment import PIPECAT_VERSION, run_one

    config = CONFIG_BY_NAME[args.config]
    if args.optout:
        config = dataclasses.replace(config, disallow_interruptions=True)
    schedule = make_schedule(config, n=args.n, seed=args.seed)
    records = []
    with open(args.out, "w", encoding="utf-8") as fh:
        for sched in schedule:
            rec = await run_one(config, sched)
            fh.write(rec.to_json() + "\n")
            fh.flush()
            records.append(rec)
            if args.verbose:
                print(
                    f"run {sched.run_index:3d} offset={sched.barge_in_offset_s:.3f}s "
                    f"phase={rec.phase_final} interrupted={rec.speech_interrupted} "
                    f"tool_started={rec.tool_started} tool_cancelled={rec.tool_cancelled} "
                    f"wall={rec.wall_time_s}s",
                    file=sys.stderr,
                )
    summary = summarise(records, mechanism=args.mechanism or "")
    d = summary.to_dict()
    d["runtime_version"] = PIPECAT_VERSION
    print(json.dumps(d))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", choices=sorted(CONFIG_BY_NAME), required=True)
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=20260902)
    p.add_argument("--out", required=True)
    p.add_argument("--mechanism", default="")
    p.add_argument("--optout", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        from loguru import logger as loguru_logger

        loguru_logger.remove()
    except ImportError:
        pass
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
