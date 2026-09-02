# What deferring the commit to turn completion actually costs

Measured 2026-09-02 against real `livekit-agents` 1.7.1 sessions, N=30,
seed 20260902, config `sync-tool`. Raw per-run records in
`deferral-sync-tool.jsonl`; regenerate with:

    experiments/frameworks/livekit_1_7_1/.venv/bin/python \
        experiments/frameworks/deferral_latency.py --n 30 --seed 20260902

## The question

The guarded design admits a phase transition when the carrying turn COMPLETES,
not when the model issues the tool call. A real-time venue's first objection is
that this must add latency. `PhaseGuard.check` costing 0.16 microseconds does
not answer that objection, because the check is not where the time goes: the
waiting is.

So the quantity to measure is the deferral window itself:

    deferral = t(speech handle resolves) - t(tool call finishes)

## The measurement

| Statistic | Value |
|---|---|
| Runs | 30, all interrupted, all produced a deferral |
| Minimum | 1,434 ms |
| Median | **1,576 ms** |
| p90 | 1,705 ms |
| Maximum | 1,761 ms |
| All positive | yes |

## What the number means, which is not what it looks like

A 1.6 second median deferral looks alarming next to a sub-second conversational
budget. It is not a latency cost, and the run parameters show why.

The scripted reply is **8.0 seconds** of audio (`REPLY_AUDIO_SECONDS`). The
barge-in fires at roughly 1.0 s, and the voice-activity detector requires 1.2 s
of speech before it triggers, so the interruption lands around 2.2 s into an
8 second turn. The tool call is emitted and completes early.

The deferral window is therefore **exactly the remainder of the turn**: the
interval between the tool finishing and the turn ending, whether it ends by
completing or by being cut short. It is not work the guard performs. Nothing
computes during it. It is the agent still talking.

Three consequences, and the third is the one that answers the objection.

1. **The window is bounded above by the reply duration**, not by any property of
   the guard. A longer reply defers longer; a shorter reply defers less. The
   guard contributes 0.16 microseconds to it, which is nine orders of magnitude
   below the window it sits inside.

2. **In an uninterrupted turn the deferral is the full remaining reply**, here up
   to 8 seconds, and the commit lands at the moment the agent stops speaking.
   That moment occurs whether or not a guard exists. The transition is admitted
   at the earliest instant at which the evidence for it is complete.

3. **No party waits during the window.** On a clean turn the agent is speaking
   through it. On an interrupted turn, which is the fault case this work is
   about, the caller is speaking through it. The deferral is filled by the
   conversation in both directions, so there is no silence attributable to it
   and nothing for a caller to perceive as delay.

## The honest cost, which is elsewhere

Deferring commit is not free, but the price is not latency. It is that a
transition whose entry condition is established by the turn's own act cannot be
admitted at issue time, and is refused there. Measured separately over the
enumeration corpus, the completion-gated design refuses **0.00 per cent** of
transitions the record justifies, while the issue-time variant refuses
**19.19 per cent** of warranted proposals, precisely those licensed by the act
of the turn carrying them. That is the trade, and it favours the completion-gated
design.

## Limits of this measurement

- One runtime (`livekit-agents` 1.7.1) and one configuration (`sync-tool`).
- The reply duration is scripted rather than synthesised by a real TTS, so the
  window's absolute size reflects the harness, not production audio. The claim
  that survives is the relation, that the window equals the remaining turn, not
  the specific milliseconds.
- Interrupted runs only. The uninterrupted case is stated above by construction
  from the same mechanism rather than measured, because the harness always
  fires a barge-in. Measuring it needs a no-barge-in schedule and is the
  obvious next run.
