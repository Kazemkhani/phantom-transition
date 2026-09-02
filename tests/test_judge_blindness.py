"""The judge-blindness experiment's own guarantees.

The generator is deterministic for a seed; the post-condition detects every
injected phantom and no control; the judge runner round-trips emit and ingest.
"""

from __future__ import annotations

import json

import pytest

from judge_blindness import generate, judge, postcondition
from phantom_transition import Facts, Phase
from phantom_transition.session import ENTRY_CONDITIONS

SEED = 20260902
N = 24


@pytest.fixture(scope="module")
def sessions():
    return generate.generate(SEED, N)


def test_generator_is_deterministic_for_a_seed():
    a = json.dumps(generate.generate(SEED, 6), sort_keys=True)
    b = json.dumps(generate.generate(SEED, 6), sort_keys=True)
    assert a == b


def test_a_different_seed_changes_the_sessions():
    a = json.dumps(generate.generate(SEED, 6), sort_keys=True)
    b = json.dumps(generate.generate(SEED + 1, 6), sort_keys=True)
    assert a != b


def test_arms_are_matched_up_to_the_interruption(sessions):
    """Phantom, control and bad-recovery sessions with the same index share
    every exchange through the interrupted one; only the recovery differs."""
    by_key = {(s["arm"], s["index"]): s for s in sessions}
    for i in range(N):
        ph, co, bad = by_key[("phantom", i)], by_key[("control", i)], by_key[("bad_recovery", i)]
        k = ph["injection_index"]
        assert co["injection_index"] == k == bad["injection_index"]
        strip = lambda e: {x: e[x] for x in ("agent_full", "caller", "interrupted", "cut_words", "tool_calls")}
        for a, b in ((ph, co), (ph, bad)):
            assert [strip(e) for e in a["exchanges"][: k + 1]] == [strip(e) for e in b["exchanges"][: k + 1]]
        assert ph["exchanges"][k + 1]["agent_full"] != co["exchanges"][k + 1]["agent_full"]


def test_ids_are_opaque(sessions):
    for s in sessions:
        assert s["arm"] not in s["id"]
        assert str(s["index"]) != s["id"][1:]
    assert len({s["id"] for s in sessions}) == len(sessions)


def test_postcondition_detects_every_phantom_and_no_control(sessions):
    results = [postcondition.check(s) for s in sessions]
    for r in results:
        if r["arm"] == "phantom":
            assert r["phantom"], r
            assert r["advanced_on_interrupted_turn"] and r["entry_unsatisfied"]
        else:
            assert not r["phantom"], r
            assert not r["advanced_on_interrupted_turn"]


def test_phantom_destination_entry_condition_is_unsatisfied_on_the_facts(sessions):
    """The post-condition's verdict re-derived from the reference model directly."""
    for s in sessions:
        if s["arm"] != "phantom":
            continue
        entry = s["trace"][s["injection_index"]]
        assert entry["interrupted"]
        target = Phase[entry["phase_after"]]
        assert target > Phase[entry["phase_before"]]
        assert not ENTRY_CONDITIONS[target](Facts(**entry["facts_after"]))


def test_every_transition_and_interruption_type_appears(sessions):
    transitions = {s["transition"] for s in sessions if s["arm"] == "phantom"}
    types = {s["interruption_type"] for s in sessions if s["arm"] == "phantom"}
    assert transitions == set(generate.TRANSITIONS)
    assert types == {"normal", "correction", "topic_switch", "pushback"}


def test_bad_recovery_continues_the_cut_off_sentence(sessions):
    for s in sessions:
        if s["arm"] != "bad_recovery":
            continue
        k = s["injection_index"]
        cut = s["exchanges"][k]
        assert s["exchanges"][k + 1]["agent_full"] == generate.remainder(cut["agent_full"], cut["cut_words"])


def test_judge_view_is_per_turn(sessions):
    """The judge sees the history through the interruption and one response."""
    s = sessions[0]
    view = judge.render_user_prompt(s)
    k = s["injection_index"]
    assert "<INTERRUPTED />" in view
    assert f"<INTERRUPTION>{s['exchanges'][k]['caller']}</INTERRUPTION>" in view
    assert s["exchanges"][k + 1]["agent_full"] in view
    if len(s["exchanges"]) > k + 2:
        assert s["exchanges"][k + 2]["agent_full"] not in view
    assert "phantom" not in view.lower()
    assert "trace" not in view.lower()


def test_judge_runner_round_trips_emit_and_ingest(sessions, tmp_path):
    prompts = tmp_path / "prompts.jsonl"
    judge.emit_prompts(sessions, prompts, batch_dir=tmp_path / "batches", batch_size=10, seed=SEED)
    lines = [json.loads(l) for l in prompts.read_text().splitlines()]
    assert len(lines) == len(sessions)
    assert {l["id"] for l in lines} == {s["id"] for s in sessions}
    assert all(set(l) == {"id", "system", "user"} for l in lines)
    batches = sorted((tmp_path / "batches").glob("batch_*.jsonl"))
    assert len(batches) == (len(sessions) + 9) // 10

    fake = tmp_path / "raw.jsonl"
    with fake.open("w") as fh:
        for l in lines:
            fh.write(json.dumps({
                "id": l["id"], "task_fulfilment": 4, "recovery_quality": 5,
                "anything_wrong": "nothing", "premature_advance": "no",
            }) + "\n")
    out = tmp_path / "judgements.jsonl"
    report = judge.ingest([fake], out, judge_name="fake judge")
    assert report["accepted"] == len(sessions) and report["rejected"] == 0
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert {r["id"] for r in rows} == {s["id"] for s in sessions}
    assert all(r["judge"] == "fake judge" for r in rows)


def test_ingest_rejects_malformed_judgements(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join([
        json.dumps({"id": "sx", "task_fulfilment": 9, "recovery_quality": 5, "anything_wrong": "", "premature_advance": "no"}),
        json.dumps({"id": "sy", "task_fulfilment": 3, "recovery_quality": 5, "anything_wrong": "", "premature_advance": "maybe"}),
        "not json",
    ]) + "\n")
    report = judge.ingest([bad], tmp_path / "out.jsonl", judge_name="fake")
    assert report["accepted"] == 0 and report["rejected"] == 3


def test_asyncio_sourced_sessions_are_deterministic_and_detected():
    """The asyncio-sourced sub-corpus: injection-turn state comes from a real
    event loop (the vendored interleaving reproduction), and the post-condition
    still detects every phantom and no control."""
    a = generate.generate(SEED + 1, 4, ("phantom", "control"), state_source="asyncio")
    b = generate.generate(SEED + 1, 4, ("phantom", "control"), state_source="asyncio")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    for s in a:
        assert s["state_source"] == "asyncio"
        assert s["transition"] == "GREETING->DISCOVERY"
        entry = s["trace"][s["injection_index"]]
        assert any("vad: caller speech detected" in e for e in entry["events"])
        assert any("tool tasks cancelled with the reply" in e for e in entry["events"])
        r = postcondition.check(s)
        assert r["phantom"] == (s["arm"] == "phantom")
        if s["arm"] == "phantom":
            assert any("has mutated the session state" in e for e in entry["events"])
