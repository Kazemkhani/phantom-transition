"""Run the framework-free text agent over the matrix configurations.

    python experiments/frameworks/text_agent/run.py --policy speculative \
        --config sync-tool --n 50 --seed 20260902 --out results.jsonl

Prints one JSON summary to stdout. Needs nothing beyond the standard library.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from harness import make_schedule, summarise  # noqa: E402
from harness.protocol import CONFIG_BY_NAME  # noqa: E402
from text_agent.agent import MECHANISM, POLICIES, run_one  # noqa: E402


async def _main(args: argparse.Namespace) -> int:
    config = CONFIG_BY_NAME[args.config]
    schedule = make_schedule(config, n=args.n, seed=args.seed)
    records = []
    with open(args.out, "w", encoding="utf-8") as fh:
        for sched in schedule:
            rec = await run_one(config, sched, policy=args.policy, time_scale=args.time_scale)
            fh.write(rec.to_json() + "\n")
            records.append(rec)
    summary = summarise(records, mechanism=MECHANISM[args.policy])
    print(json.dumps(summary.to_dict()))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--policy", choices=POLICIES, required=True)
    p.add_argument("--config", choices=sorted(CONFIG_BY_NAME), required=True)
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=20260902)
    p.add_argument("--time-scale", type=float, default=1.0)
    p.add_argument("--out", required=True)
    return asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
