# The second judge is not blind, and that changes the claim

Run 2026-09-03. 600 sessions, N=200 per arm, seed 20260902, same corpus and same
rubric as the Haiku run. Judge: `claude-sonnet-5` via Claude Code subagents, six
agents, four batches each, 600 of 600 judgements accepted with zero rejects.

## The result

| Judge | Arm | Flags premature advance | Post-condition |
|---|---|---:|---:|
| Haiku 4.5 | phantom | 73.5% | 100% |
| Haiku 4.5 | control | 0.0% | 0.0% |
| Haiku 4.5 | bad recovery | 4.0% | 0.0% |
| **Sonnet 5** | **phantom** | **100.0%** | 100% |
| **Sonnet 5** | **control** | **0.0%** | 0.0% |
| **Sonnet 5** | **bad recovery** | **0.0%** | 0.0% |

Detection by which transition was faked:

| Judge | GREET->DISC | DISC->PITCH (0) | DISC->PITCH (1) | PITCH->CLOSE |
|---|---:|---:|---:|---:|
| Haiku 4.5 | 26.0% | 93.8% | 77.8% | 97.9% |
| **Sonnet 5** | **100%** | **100%** | **100%** | **100%** |

Not a degenerate classifier. Raw counts: `phantom -> yes` 200 of 200,
`control -> no` 200 of 200, `bad_recovery -> no` 200 of 200. A judge defaulting
to "yes" would score 100 per cent on the controls too. This one separates all
three arms perfectly.

## What this falsifies

The paper currently argues that a language-model judge is **unevenly blind** to
phantom transitions, worst on the first transition of a call, which is where a
deployment meets the fault most often. That claim rests on the Haiku gradient of
26 to 98 per cent.

**It does not hold for a capable judge.** Sonnet 5 detects every injected phantom
transition, at every position in the call, under every interruption type, with no
false positives on either control arm. The blindness is a property of the weaker
judge, not of transcript-based evaluation.

We ran this experiment to strengthen the paper. It weakened a claim instead, and
reporting it that way is the whole reason for running it.

## What survives, stated no more strongly than the evidence

1. **Detection is strongly model-dependent.** Between two judges on an identical
   corpus and rubric, detection moves from 73.5 per cent and uneven to 100 per
   cent and flat. Evaluation quality here is bought, not given.
2. **The post-condition equals the best judge at none of the cost.** It matches
   Sonnet's perfect separation with no model, no API call, no prompt, no variance
   between runs, and no per-call price. Against the cheaper judge it is strictly
   better.
3. **The deployment argument, which is now economic rather than epistemic.**
   Nobody runs a frontier-class judge across 100 per cent of production call
   volume; sampled cheap judging is the norm at scale. So a deployment evaluating
   on an affordable judge is measurably blind to this fault, while one paying for
   a frontier judge is not. That is a real and useful claim, and it is a claim
   about cost, not about what transcripts can reveal.

## What the paper must stop saying

Any unqualified form of "a judge cannot see this" or "the transcript is healthy
by construction so a reader of it cannot detect the fault". The second judge read
the same transcripts and detected every case. The honest version is that
detection depends on judge capability, the post-condition does not, and the gap
matters most exactly where cost forces the cheaper judge.

## Limits

- Two judges, one corpus, one rubric, one seed.
- Both judges were driven as Claude Code subagents rather than through the API,
  so sampling parameters were not controlled.
- The corpus is synthetic and generated from the same model of the fault the
  post-condition checks, so post-condition recall on the phantom arm is analytic
  rather than measured. The controls are the informative columns.
