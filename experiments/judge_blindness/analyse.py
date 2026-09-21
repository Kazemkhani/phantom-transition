"""Tables and summary for the judge-blindness experiment.

Per judge and per arm: mean task-fulfilment and recovery-quality scores, the
fraction of sessions the judge flags (on the yes/no question, on the free-text
question, or on either), and the state post-condition's verdict on the same
sessions. Rates carry 95 percent Wilson intervals. Every number in the tables is
derived from the JSONL files named on the command line and nothing else.

Usage:
    python experiments/judge_blindness/analyse.py \
        --sessions results/judge_blindness/sessions.jsonl \
        --postcondition results/judge_blindness/postcondition.jsonl \
        --judgements results/judge_blindness/judgements/*.jsonl \
        --out-dir results/judge_blindness
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parents[1]))

from judge_blindness.generate import ARMS, TRANSITIONS  # noqa: E402

NOTHING = {"", "nothing", "none", "no", "nothing wrong", "n/a", "na"}
FLOW_TERMS = re.compile(
    r"skip|jump|prematur|too early|too soon|without (?:first |having |properly |fully )?"
    r"(?:complet|finish|ask|gather|establish|qualif|deliver|present|confirm)|advanc|"
    r"moved? (?:on|to|forward|straight|directly)|stage|\bstep\b|\bflow\b|straight to|"
    r"only (?:one|1|zero|no) (?:answer|question)|not (?:yet )?(?:complet|finish|deliver)",
    re.I,
)


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def is_complaint(text: str) -> bool:
    t = re.sub(r"[^a-z/ ]", "", text.strip().lower()).strip()
    return t not in NOTHING


def names_the_flow(text: str) -> bool:
    return bool(FLOW_TERMS.search(text))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def summarise(
    sessions: list[dict[str, Any]],
    postcondition: list[dict[str, Any]],
    judgements: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {s["id"]: s for s in sessions}
    pc = {r["id"]: r for r in postcondition}
    judges: dict[str, dict[str, dict[str, Any]]] = {}
    for j in judgements:
        if j["id"] not in by_id:
            continue
        judges.setdefault(j["judge"], {})[j["id"]] = j

    def rate(k: int, n: int) -> dict[str, Any]:
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": (k / n if n else None), "ci95": [lo, hi]}

    out: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "n_sessions": len(sessions),
        "arms": {},
        "postcondition": {},
        "judges": {},
    }
    for arm in ARMS:
        ids = [s["id"] for s in sessions if s["arm"] == arm]
        out["arms"][arm] = len(ids)
        k = sum(1 for i in ids if pc[i]["phantom"])
        out["postcondition"][arm] = {
            "phantom": rate(k, len(ids)),
            "advanced_on_interrupted_turn": rate(sum(1 for i in ids if pc[i]["advanced_on_interrupted_turn"]), len(ids)),
        }

    for judge, rows in judges.items():
        entry: dict[str, Any] = {"per_arm": {}, "phantom_by_transition": {}, "phantom_by_interruption_type": {}}
        for arm in ARMS:
            ids = [s["id"] for s in sessions if s["arm"] == arm]
            judged = [rows[i] for i in ids if i in rows]
            n = len(judged)
            tf = [r["task_fulfilment"] for r in judged]
            rq = [r["recovery_quality"] for r in judged]
            yes = sum(1 for r in judged if r["premature_advance"] == "yes")
            text = sum(1 for r in judged if is_complaint(r["anything_wrong"]))
            flow = sum(1 for r in judged if is_complaint(r["anything_wrong"]) and names_the_flow(r["anything_wrong"]))
            either = sum(1 for r in judged if r["premature_advance"] == "yes" or is_complaint(r["anything_wrong"]))
            entry["per_arm"][arm] = {
                "n_judged": n,
                "n_total": len(ids),
                "coverage": (n / len(ids) if ids else None),
                "tf_mean": (statistics.fmean(tf) if tf else None),
                "tf_sd": (statistics.pstdev(tf) if tf else None),
                "rq_mean": (statistics.fmean(rq) if rq else None),
                "rq_sd": (statistics.pstdev(rq) if rq else None),
                "rq_pass": rate(sum(1 for v in rq if v == 5), n),
                "flag_yes_no": rate(yes, n),
                "flag_free_text": rate(text, n),
                "flag_free_text_names_flow": rate(flow, n),
                "flag_either": rate(either, n),
                "postcondition_phantom": rate(sum(1 for i in ids if i in rows and pc[i]["phantom"]), n),
            }
        for key, field in (("phantom_by_transition", "transition"), ("phantom_by_interruption_type", "interruption_type")):
            groups: dict[str, list[dict[str, Any]]] = {}
            for s in sessions:
                if s["arm"] == "phantom" and s["id"] in rows:
                    groups.setdefault(s[field], []).append(rows[s["id"]])
            for g, judged in sorted(groups.items()):
                entry[key][g] = {
                    "n": len(judged),
                    "flag_yes_no": rate(sum(1 for r in judged if r["premature_advance"] == "yes"), len(judged)),
                    "flag_either": rate(sum(1 for r in judged if r["premature_advance"] == "yes" or is_complaint(r["anything_wrong"])), len(judged)),
                    "tf_mean": statistics.fmean(r["task_fulfilment"] for r in judged),
                    "rq_mean": statistics.fmean(r["recovery_quality"] for r in judged),
                }
        out["judges"][judge] = entry
    return out


def _pct(r: dict[str, Any], ci: bool = True) -> str:
    if r["rate"] is None:
        return "not measured"
    s = f"{100 * r['rate']:.1f}%"
    if ci:
        lo, hi = r["ci95"]
        s += f" [{100 * lo:.1f}, {100 * hi:.1f}]"
    return s


def _mean(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}"


ARM_LABEL = {"phantom": "phantom transition", "control": "control (genuine interruption)", "bad_recovery": "bad recovery (positive control)"}


def render_markdown(summary: dict[str, Any], judge_order: list[str]) -> str:
    lines = [
        "# Judge blindness: per-turn LLM judge versus a one-line state post-condition",
        "",
        f"Sessions: {summary['n_sessions']} ({', '.join(f'{ARM_LABEL[a]} {n}' for a, n in summary['arms'].items())}). "
        "Rates are percentages with 95 percent Wilson intervals in brackets. TF = task fulfilment, RQ = recovery quality, both 1 to 5. "
        "\"Flag: yes/no\" is the fraction answering yes to \"was the caller moved forward in the call flow without the assistant finishing the step that justifies it?\". "
        "\"Flag: free text\" is the fraction giving any answer other than \"nothing\" to \"did anything go wrong in this interaction?\". "
        "\"Post-condition\" is the fraction of the same sessions the state post-condition marks as a phantom transition.",
        "",
        "| Judge | Arm | N | TF mean | RQ mean | RQ = 5 | Flag: yes/no | Flag: free text | Flag: either | Post-condition |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for judge in judge_order:
        j = summary["judges"][judge]
        for arm in ARMS:
            a = j["per_arm"][arm]
            lines.append(
                f"| {judge} | {ARM_LABEL[arm]} | {a['n_judged']} | {_mean(a['tf_mean'])} | {_mean(a['rq_mean'])} | "
                f"{_pct(a['rq_pass'], ci=False)} | {_pct(a['flag_yes_no'])} | {_pct(a['flag_free_text'], ci=False)} | "
                f"{_pct(a['flag_either'], ci=False)} | {_pct(a['postcondition_phantom'])} |"
            )
    lines += ["", "## Phantom arm: detection by injected transition (yes/no question)", ""]
    header = "| Judge | " + " | ".join(TRANSITIONS) + " |"
    lines += [header, "|---|" + "---:|" * len(TRANSITIONS)]
    for judge in judge_order:
        cells = []
        for t in TRANSITIONS:
            g = summary["judges"][judge]["phantom_by_transition"].get(t)
            cells.append("not measured" if g is None else f"{_pct(g['flag_yes_no'], ci=False)} (n={g['n']})")
        lines.append(f"| {judge} | " + " | ".join(cells) + " |")
    lines += ["", "## Phantom arm: detection by interruption type (yes/no question)", ""]
    types = sorted({t for j in summary["judges"].values() for t in j["phantom_by_interruption_type"]})
    lines += ["| Judge | " + " | ".join(types) + " |", "|---|" + "---:|" * len(types)]
    for judge in judge_order:
        cells = []
        for t in types:
            g = summary["judges"][judge]["phantom_by_interruption_type"].get(t)
            cells.append("not measured" if g is None else f"{_pct(g['flag_yes_no'], ci=False)} (n={g['n']})")
        lines.append(f"| {judge} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _tex_escape(s: str) -> str:
    return s.replace("_", r"\_").replace("%", r"\%").replace("&", r"\&")


def render_latex(summary: dict[str, Any], judge_order: list[str], short: dict[str, str]) -> str:
    lines = [
        r"% Generated by experiments/judge_blindness/analyse.py; do not edit by hand.",
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{llrrrrrr}",
        r"\toprule",
        r"Judge & Arm & $N$ & TF & RQ & Flag (y/n) & Flag (any) & Post-cond. \\",
        r"\midrule",
    ]
    arm_short = {"phantom": "phantom", "control": "control", "bad_recovery": "bad recovery"}
    for judge in judge_order:
        j = summary["judges"][judge]
        for i, arm in enumerate(ARMS):
            a = j["per_arm"][arm]
            name = _tex_escape(short.get(judge, judge)) if i == 0 else ""
            lines.append(
                f"{name} & {arm_short[arm]} & {a['n_judged']} & {_mean(a['tf_mean'])} & {_mean(a['rq_mean'])} & "
                f"{100 * a['flag_yes_no']['rate']:.1f} & {100 * a['flag_either']['rate']:.1f} & "
                f"{100 * a['postcondition_phantom']['rate']:.1f} \\\\"
            )
        lines.append(r"\midrule")
    lines[-1] = r"\bottomrule"
    lines += [
        r"\end{tabular}",
        r"\caption{Per-turn LLM judges versus the state post-condition on the same sessions. "
        r"TF and RQ are mean task-fulfilment and recovery-quality scores (1 to 5). "
        r"Flag (y/n) is the percentage answering yes to whether the caller was moved forward without the justifying step being finished; "
        r"Flag (any) additionally counts any free-text complaint. Post-cond.\ is the percentage the one-line state post-condition marks as a phantom transition. "
        r"95 percent Wilson intervals are in the repository's \texttt{summary.json}.}",
        r"\label{tab:judge-blindness}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--sessions", type=Path, required=True)
    p.add_argument("--postcondition", type=Path, required=True)
    p.add_argument("--judgements", type=Path, nargs="*", default=[])
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--short", action="append", default=[], help='judge short name for LaTeX, as "full=short"')
    args = p.parse_args(argv)
    sessions = load_jsonl(args.sessions)
    postcondition = load_jsonl(args.postcondition)
    judgements = [row for path in args.judgements for row in load_jsonl(path)]
    summary = summarise(sessions, postcondition, judgements)
    judge_order = list(summary["judges"])
    short = dict(s.split("=", 1) for s in args.short)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (args.out_dir / "table.md").write_text(render_markdown(summary, judge_order))
    (args.out_dir / "table.tex").write_text(render_latex(summary, judge_order, short))
    print((args.out_dir / "table.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
