# Phantom transitions

A minimal reproduction, and fix, for a failure mode in phase-gated multi-agent voice systems.

```
pip install -e ".[dev]" && pytest -v
```

21 tests. No dependencies beyond pytest.

## The fault

Splitting a voice agent into one specialised agent per phase of a call is a good design. Each agent gets a smaller brief, and handing off between them is a tool call.

It has a failure mode nobody designs for.

A real-time voice agent generates its next turn while the caller is still speaking, because waiting for end-of-speech costs latency the caller can hear. When the caller barges in, the speech and reasoning already in flight are discarded. But a tool call issued a moment earlier has already executed. If that tool call was a phase handoff, **the conversation has silently advanced to a stage the caller never triggered**, and nothing in the transcript says so.

```python
session = Session(preemptive_generation=True, cancel_handoff_on_interrupt=False, guard_enabled=False)
session.handle_turn(Turn("hello"))
assert session.phase is Phase.GREETING

session.handle_turn(Turn("wait, sorry", interrupted=True),
                    [ToolCall("advance_phase", {"target": Phase.DISCOVERY})])

assert session.phase is Phase.DISCOVERY   # the caller did nothing to cause this
```

`tests/test_phantom_transition.py::test_phantom_transition_reproduces`

The caller said "wait, sorry". The agent's reply was thrown away. The call moved on anyway.

## Why it matters beyond voice

The fault generalises to any agent that acts on a tool call issued before the user has finished speaking. Voice makes it visible because barge-in is constant, but the shape is general: an action commits, the context that justified it is then discarded, and no record distinguishes the two.

In a regulated deployment a silent, unrequested state change is an auditability failure rather than a cosmetic one. An auditor reconstructing why a decision was made cannot see that the state moved for a reason the transcript does not contain.

## The three fixes

**1. Cancel the handoff after execution when the turn was interrupted.** The runtime only learns a turn was interrupted after the tool has run, so the correction has to be a rollback rather than a precondition.

**2. Disable pre-emptive generation.** This closes the fault at source and costs latency. It is the right default and the wrong optimisation to reach for first.

**3. Guard phase progression at the tool layer, not in the prompt.** This is the one that matters. A prompt instruction not to skip phases is a request. A guard at the tool boundary is a constraint.

```python
class PhaseGuard:
    def check(self, current: Phase, target: Phase, facts: Facts) -> tuple[bool, str]:
        ...
```

Note what the signature does not accept: the utterance. The guard cannot read what the caller said, so no phrasing can talk the system past it, and no instruction in the model's context can either. Its only inputs are the current phase and a `Facts` record written by the runtime from observed events.

Entry conditions are declarative:

| Target | Requires |
| --- | --- |
| `DISCOVERY` | greeting delivered |
| `PITCH` | at least two discovery answers recorded |
| `CLOSE` | pitch delivered |

Progression is forward only and strictly one phase at a time.

## The ten guard tests

`tests/test_guard.py` establishes that the guard has no bypass path reachable through the public API.

| # | Attempt |
| --- | --- |
| 1 | Skip a phase |
| 2 | Jump straight to the final phase |
| 3 | Advance without entry conditions met |
| 4 | Move backwards |
| 5 | Advance to the current phase |
| 6 | Five prompt injections in the utterance |
| 7 | Forged `authorised`, `override` and `force` arguments |
| 8 | Malformed phase values, including the string `"CLOSE"` and the integer `3` |
| 9 | Write guard facts from the utterance, twenty times |
| 10 | Fifty consecutive interrupted turns, each carrying a handoff |

Two further tests check the guard is a gate rather than a wall: the legitimate path still traverses all four phases, and the guard is pure.

## What this is not

This is a standalone reproduction written to isolate one fault, not the production system it was found in. It carries no LiveKit, no model provider and no audio, because none of those are necessary to demonstrate the mechanism, and including them would make the failure harder to see rather than easier.

The production system this came from is a bilingual Arabic and English voice agent for qualification calls, carrying a 334-test suite of which ten are the guard tests reproduced here.

## Licence

MIT. Amir Hossein Kazemkhani, [amirkazemkhani.com](https://amirkazemkhani.com).
