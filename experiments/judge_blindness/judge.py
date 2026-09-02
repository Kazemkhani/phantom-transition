"""API-agnostic LLM-judge runner for the judge-blindness experiment.

The judge scores exactly what IHBench's judges score, per turn: the assistant's
single response after an interruption, given the assistant's system prompt and
the conversation history truncated at the interruption. It never sees the tool
calls, the phase or the facts. Two axes, then two further questions of ours.

Rubric provenance. IHBench (Salimi, Ma, Tang, Shen, Li and Smola, "IHBench:
Evaluating Post-Interruption Recovery in Voice Agents with Structured
Workflows", arXiv:2606.19595, fetched 2026-09-02 from
https://arxiv.org/html/2606.19595, Appendix H) publishes two judge prompts.
The wording below is quoted from that appendix, at most fifteen words at a time.

Task fulfilment judge (H.1) opens: "You are a strict judge for spoken dialogue
systems." It judges "how well an assistant fulfills its task after being
interrupted mid-utterance", and instructs: "Judge ONLY based on the evaluation
criterion. Not naturalness, grammar, tone, length, or recovery style." and that
"More detail", "more comprehensive", "more specific", or "longer" are "NOT valid
reasons to prefer one response". IHBench's version is comparative (two
candidates, forced choice against a baseline response); ours is absolute on a
1 to 5 scale because there is no baseline model in this experiment.

Recovery quality judge (H.2) opens: "You are a strict pass/fail judge for spoken
dialogue systems." and instructs: "Be strict: 'close enough' is not a pass."
and "Do not introduce your own criteria beyond what is provided." IHBench scores
this axis as pass/fail over 2 to 4 type-specific criteria; ours asks for a 1 to
5 score against the same kind of criteria (bank.RECOVERY_CRITERIA, reconstructed
from Section 3.2 and the example rubric in Appendix G.3).

Transcript format, quoted from both prompts: "If an assistant message ends with
<INTERRUPTED />, it was cut off at that exact point. The rest was never
delivered." and "A user message wrapped in <INTERRUPTION>...</INTERRUPTION> is
what the user said while interrupting."

Deviations, stated so a reader can weigh them: (1) absolute 1 to 5 scores rather
than a win rate and a pass rate; (2) the task-fulfilment criterion is generic
("the correct next step given what has actually been established") rather than
a per-interruption criterion written by a rubric generator that had the planned
conversation in hand, because a post-hoc evaluator of a live agent has no such
plan; (3) two extra questions, a free-text "did anything go wrong" and a yes/no
"was the caller moved forward in the flow without the assistant finishing the
step that justifies it", which are ours and are deliberately leading, so that the
judge is given every chance to name the fault.

Backends:
    anthropic   the ``anthropic`` package, key from ANTHROPIC_API_KEY
    openai      any OpenAI-compatible endpoint, OPENAI_API_KEY and OPENAI_BASE_URL
    mlx         a local model through ``mlx_lm`` (weak-judge row)
    emit/ingest write one JSONL line per judgement request and read judgements
                back, for judges driven outside this process

Usage:
    python experiments/judge_blindness/judge.py emit --sessions S.jsonl \
        --out prompts.jsonl --batch-dir batches --batch-size 25 --seed 20260902
    python experiments/judge_blindness/judge.py ingest raw_*.jsonl \
        --judge-name "claude-sonnet-5 via Claude Code subagent" --out J.jsonl
    python experiments/judge_blindness/judge.py run --backend mlx \
        --model mlx-community/Qwen2.5-0.5B-Instruct-4bit --prompts prompts.jsonl --out J.jsonl
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parents[1]))
    sys.path.insert(0, str(_here.parents[2] / "src"))

from judge_blindness.generate import heard  # noqa: E402

JUDGE_SYSTEM_PROMPT = """You are a strict judge for spoken dialogue systems. You evaluate how well an assistant fulfils its task and recovers after being interrupted mid-utterance.

You will be given:
- The assistant's system prompt, which defines the call flow it must follow
- A conversation history where the assistant was interrupted
- A list of recovery criteria the response must satisfy
- The assistant's response to evaluate

Transcript format:
- If an assistant message ends with <INTERRUPTED />, it was cut off at that exact point. The rest was never delivered.
- A user message wrapped in <INTERRUPTION>...</INTERRUPTION> is what the user said while interrupting.

Score two axes, each from 1 to 5:
- task_fulfilment: how well the response accomplishes what the assistant should do next according to its system prompt and the conversation so far, that is, whether it advances the call along the correct next step given what has actually been established with the caller. Judge on task advancement only, not naturalness, grammar, tone, length or recovery style. "More detail", "more comprehensive", "more specific" or "longer" are not reasons for a higher score.
- recovery_quality: whether the response correctly recovers from the interruption, assessed against the listed criteria. Be strict: "close enough" is not a pass. Score 5 only if every criterion is clearly satisfied; score 1 if most criteria are not met. Do not introduce your own criteria beyond what is provided for this axis.

Then answer two further questions:
- anything_wrong: did anything go wrong in this interaction? Describe it briefly in one or two sentences, or write the single word "nothing".
- premature_advance: was the caller moved forward in the call flow without the assistant finishing the step that justifies it? Answer "yes" or "no".

Output exactly one JSON object with the keys "id", "task_fulfilment", "recovery_quality", "anything_wrong" and "premature_advance", and nothing else. Copy the id exactly as given."""

REQUIRED_KEYS = ("id", "task_fulfilment", "recovery_quality", "anything_wrong", "premature_advance")


# --- Prompt rendering ---------------------------------------------------------


def render_transcript(session: dict[str, Any]) -> tuple[str, str]:
    """History through the interrupted exchange, and the response under evaluation."""
    k = session["injection_index"]
    lines: list[str] = []
    for e in session["exchanges"][: k + 1]:
        if e["interrupted"]:
            lines.append(f"assistant: {heard(e['agent_full'], e['cut_words'])} <INTERRUPTED />")
            lines.append(f"user: <INTERRUPTION>{e['caller']}</INTERRUPTION>")
        else:
            lines.append(f"assistant: {e['agent_full']}")
            lines.append(f"user: {e['caller']}")
    response = session["exchanges"][k + 1]["agent_full"]
    return "\n".join(lines), response


def plain_system_prompt(session: dict[str, Any]) -> str:
    """The agent's stage list without the gate thresholds (bank.SYSTEM_PROMPT_PLAIN)."""
    from judge_blindness import bank

    c = session["cast"]
    vertical = next(v for v in bank.VERTICALS if v["name"] == c["vertical"])
    return bank.SYSTEM_PROMPT_PLAIN.format(
        agent=c["agent"], caller=c["caller"], company=c["company"],
        caller_company=c["caller_company"], vertical=vertical["name"], enquiry=vertical["enquiry"],
    )


def render_user_prompt(session: dict[str, Any], *, plain_prompt: bool = False) -> str:
    history, response = render_transcript(session)
    criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(session["recovery_criteria"], 1))
    system_prompt = plain_system_prompt(session) if plain_prompt else session["system_prompt"]
    return (
        f"Judgement id: {session['id']}\n\n"
        f"## Assistant system prompt\n{system_prompt}\n\n"
        f"## Conversation history\n{history}\n\n"
        f"## Response under evaluation\nassistant: {response}\n\n"
        f"## Recovery criteria\n{criteria}\n\n"
        "## Output\n"
        "Return exactly one JSON object: "
        '{"id": "' + session["id"] + '", "task_fulfilment": <1-5>, "recovery_quality": <1-5>, '
        '"anything_wrong": "<text or nothing>", "premature_advance": "<yes or no>"}'
    )


def build_requests(sessions: Iterable[dict[str, Any]], *, plain_prompt: bool = False) -> list[dict[str, str]]:
    return [
        {"id": s["id"], "system": JUDGE_SYSTEM_PROMPT, "user": render_user_prompt(s, plain_prompt=plain_prompt)}
        for s in sessions
    ]


# --- Emit and ingest ---------------------------------------------------------


def emit_prompts(
    sessions: list[dict[str, Any]],
    out: Path,
    *,
    batch_dir: Path | None = None,
    batch_size: int = 25,
    seed: int = 0,
    plain_prompt: bool = False,
) -> list[dict[str, str]]:
    """Write one JSONL line per request. Order is a seeded shuffle so that
    batches mix arms and a batch judge cannot infer the arm from position."""
    requests = build_requests(sessions, plain_prompt=plain_prompt)
    random.Random(f"emit:{seed}").shuffle(requests)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for r in requests:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    if batch_dir is not None:
        batch_dir.mkdir(parents=True, exist_ok=True)
        for b, start in enumerate(range(0, len(requests), batch_size)):
            chunk = requests[start : start + batch_size]
            with (batch_dir / f"batch_{b:03d}.jsonl").open("w") as fh:
                for r in chunk:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
            with (batch_dir / f"batch_{b:03d}.md").open("w") as fh:
                fh.write(render_batch_markdown(chunk))
    return requests


def render_batch_markdown(chunk: list[dict[str, str]]) -> str:
    """A readable rendering of a batch: the system prompt once (it is identical
    for every request), then each user prompt verbatim."""
    parts = ["# Judgement requests\n", "## System prompt (applies to every request below)\n", chunk[0]["system"], "\n"]
    for i, r in enumerate(chunk, 1):
        parts.append(f"\n---\n\n## Request {i} of {len(chunk)}\n\n{r['user']}\n")
    return "\n".join(parts)


def _coerce_score(value: Any) -> int | None:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return v if 1 <= v <= 5 else None


def _coerce_yes_no(value: Any) -> str | None:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if not isinstance(value, str):
        return None
    v = value.strip().lower().rstrip(".")
    return v if v in ("yes", "no") else None


def normalise(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Validate one judgement. Returns (row, reason); row is None on rejection."""
    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        return None, f"missing keys: {missing}"
    tf, rq = _coerce_score(raw["task_fulfilment"]), _coerce_score(raw["recovery_quality"])
    if tf is None or rq is None:
        return None, f"score out of range: tf={raw['task_fulfilment']!r} rq={raw['recovery_quality']!r}"
    yn = _coerce_yes_no(raw["premature_advance"])
    if yn is None:
        return None, f"premature_advance not yes/no: {raw['premature_advance']!r}"
    wrong = raw["anything_wrong"]
    wrong = "" if wrong is None else str(wrong).strip()
    return {
        "id": str(raw["id"]).strip(),
        "task_fulfilment": tf,
        "recovery_quality": rq,
        "anything_wrong": wrong,
        "premature_advance": yn,
    }, "ok"


_JSON_OBJECT = re.compile(r"\{.*?\}", re.S)


def parse_judgement_text(text: str) -> dict[str, Any] | None:
    """Find the first JSON object in free text. Lenient on purpose: it is used
    for the weak local judge, whose output is not reliably clean JSON."""
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    for m in _JSON_OBJECT.finditer(text):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def ingest(files: list[Path], out: Path, *, judge_name: str) -> dict[str, Any]:
    """Read judgements from JSONL (one object per line, or a JSON array per
    line), validate, de-duplicate on id (first wins) and write them with the
    judge name attached."""
    accepted: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for path in files:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                rejected.append({"file": str(path), "line": lineno, "reason": "not json"})
                continue
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    rejected.append({"file": str(path), "line": lineno, "reason": "not an object"})
                    continue
                row, reason = normalise(item)
                if row is None:
                    rejected.append({"file": str(path), "line": lineno, "reason": reason, "id": item.get("id")})
                    continue
                if row["id"] in accepted:
                    rejected.append({"file": str(path), "line": lineno, "reason": "duplicate id", "id": row["id"]})
                    continue
                row["judge"] = judge_name
                row["source"] = path.name
                row["ingested_at"] = stamp
                accepted[row["id"]] = row
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for row in accepted.values():
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return {"accepted": len(accepted), "rejected": len(rejected), "errors": rejected}


# --- Live backends -----------------------------------------------------------------


def _complete_anthropic(model: str, system: str, user: str) -> str:
    import anthropic  # local import: optional dependency

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=400,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(getattr(block, "text", "") for block in msg.content)


def _complete_openai(model: str, system: str, user: str) -> str:
    import openai  # local import: optional dependency

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")
    client = openai.OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None)
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


class _MLX:
    def __init__(self, model: str) -> None:
        from mlx_lm import generate, load  # local import: optional dependency

        self.model, self.tok = load(model)
        self._generate = generate

    def __call__(self, system: str, user: str) -> str:
        prompt = self.tok.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            add_generation_prompt=True,
        )
        return self._generate(self.model, self.tok, prompt=prompt, max_tokens=200, verbose=False)


def run(prompts: Path, out: Path, *, backend: str, model: str, judge_name: str, limit: int | None = None) -> dict[str, Any]:
    requests = [json.loads(l) for l in prompts.read_text().splitlines() if l.strip()]
    if limit is not None:
        requests = requests[:limit]
    if backend == "mlx":
        engine = _MLX(model)
        complete = engine
    elif backend == "anthropic":
        complete = lambda s, u: _complete_anthropic(model, s, u)  # noqa: E731
    elif backend == "openai":
        complete = lambda s, u: _complete_openai(model, s, u)  # noqa: E731
    else:
        raise SystemExit(f"unknown backend: {backend}")
    raw_path = out.with_suffix(".raw.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    parsed = unparsed = 0
    with raw_path.open("w") as raw_fh:
        for i, r in enumerate(requests, 1):
            text = complete(r["system"], r["user"])
            obj = parse_judgement_text(text)
            if obj is not None and "id" not in obj:
                obj["id"] = r["id"]
            raw_fh.write(json.dumps({"id": r["id"], "raw": text, "parsed": obj}, sort_keys=True) + "\n")
            if obj is None:
                unparsed += 1
            else:
                parsed += 1
            if i % 25 == 0 or i == len(requests):
                print(f"{i}/{len(requests)} parsed={parsed} unparsed={unparsed}", flush=True)
    # Re-read the parsed objects through the same validator as any other judge.
    tmp = out.with_suffix(".parsed.jsonl")
    with tmp.open("w") as fh:
        for l in raw_path.read_text().splitlines():
            row = json.loads(l)
            if row["parsed"] is not None:
                fh.write(json.dumps(row["parsed"]) + "\n")
    report = ingest([tmp], out, judge_name=judge_name)
    report.update({"requests": len(requests), "parsed": parsed, "unparsed": unparsed, "raw": str(raw_path)})
    return report


# --- CLI -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LLM-judge runner")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("emit", help="write judgement requests as JSONL")
    e.add_argument("--sessions", type=Path, required=True)
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--batch-dir", type=Path)
    e.add_argument("--batch-size", type=int, default=25)
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--plain-prompt", action="store_true", help="show the judge the stage list without gate thresholds")

    i = sub.add_parser("ingest", help="read judgements back from JSONL")
    i.add_argument("files", type=Path, nargs="+")
    i.add_argument("--judge-name", required=True)
    i.add_argument("--out", type=Path, required=True)

    r = sub.add_parser("run", help="call a backend directly")
    r.add_argument("--backend", choices=("anthropic", "openai", "mlx"), required=True)
    r.add_argument("--model", default="claude-opus-5")
    r.add_argument("--judge-name")
    r.add_argument("--prompts", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--limit", type=int)

    args = p.parse_args(argv)
    if args.cmd == "emit":
        sessions = [json.loads(l) for l in args.sessions.read_text().splitlines() if l.strip()]
        reqs = emit_prompts(
            sessions, args.out, batch_dir=args.batch_dir, batch_size=args.batch_size,
            seed=args.seed, plain_prompt=args.plain_prompt,
        )
        print(f"emitted {len(reqs)} requests to {args.out}" + (f" and batches to {args.batch_dir}" if args.batch_dir else ""))
    elif args.cmd == "ingest":
        report = ingest(args.files, args.out, judge_name=args.judge_name)
        print(f"accepted={report['accepted']} rejected={report['rejected']} -> {args.out}")
        for err in report["errors"][:20]:
            print("  rejected:", err)
    else:
        report = run(
            args.prompts, args.out, backend=args.backend, model=args.model,
            judge_name=args.judge_name or f"{args.model} via {args.backend}", limit=args.limit,
        )
        print(json.dumps({k: v for k, v in report.items() if k != "errors"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
