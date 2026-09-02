"""CLI: run N seeded runs of one configuration against the installed livekit-agents.

    <venv>/bin/python experiments/frameworks/livekit_shared/run.py \
        --config sync-tool --n 50 --seed 20260902 --out results.jsonl

Prints one JSON summary to stdout; per-run records go to --out as JSON lines.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from harness import make_schedule, summarise  # noqa: E402
from harness.protocol import CONFIG_BY_NAME  # noqa: E402


async def _main(args: argparse.Namespace) -> int:
    from livekit_shared.experiment import LK_VERSION, run_one

    config = CONFIG_BY_NAME[args.config]
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
                    f"tool_finished={rec.tool_finished} record_kept={rec.tool_record_in_context} "
                    f"playback={rec.playback_seconds}s wall={rec.wall_time_s}s",
                    file=sys.stderr,
                )
    summary = summarise(records, mechanism=args.mechanism or "")
    d = summary.to_dict()
    d["runtime_version"] = LK_VERSION
    print(json.dumps(d))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", choices=sorted(CONFIG_BY_NAME), required=True)
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=20260902)
    p.add_argument("--out", required=True)
    p.add_argument("--mechanism", default="")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("livekit").setLevel(logging.ERROR)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
