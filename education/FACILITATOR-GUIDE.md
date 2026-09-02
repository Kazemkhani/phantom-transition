# Facilitator guide: interrupted turns can still advance state

A 45-minute session for advanced undergraduates who know Python, have met a finite state machine, and have seen a tool-calling language model. One notebook, no dependencies for Sections 1 to 6, Hypothesis for Section 7 (`pip install hypothesis`).

## Learning objectives, by level

| Level | The learner will be able to |
|---|---|
| Remember | State the two phases of a contribution (presentation, acceptance) and what "grounded" means. |
| Understand | Explain why an interrupted turn must not advance state, and why the transcript looks healthy when it does. |
| Apply | Reproduce the fault in forty lines and implement the guard whose signature excludes the utterance. |
| Analyse | Compare Gate-in-Prompt, Cancel-on-Interrupt and Evidence-Gated Admission by the failure each forecloses and the cost each carries. |
| Evaluate | Judge when exhaustive enumeration is the right instrument and when property-based testing is, and state the bound of an exhaustive claim. |
| Create | Extend the move alphabet and the invariant set, re-run both instruments, and break the guard on purpose to see each instrument fail. |

## Session plan

| Minutes | Activity | Notebook |
|---|---|---|
| 0 to 5 | The 1974 and 1991 framing. Ask: what does it mean for something to have been said? | Introduction |
| 5 to 12 | Run the failure live. Pause on the log: two events, nothing relating them. | Sections 1 and 2 |
| 12 to 17 | Why per-turn evaluation and transcript audits are blind by construction. | Section 3 |
| 17 to 27 | The three patterns. Run Gate-in-Prompt and Cancel-on-Interrupt; let students predict each result before running. | Section 4 |
| 27 to 33 | The guard. Read the signature aloud before the body. Run the forged-arguments case. | Section 5 |
| 33 to 40 | Two instruments: exhaustive enumeration, then Hypothesis finding and shrinking the planted bug. | Sections 6 and 7 |
| 40 to 45 | Set the exercise; discuss what generalises beyond voice. | Sections 8 and 9 |

## Common misconceptions

1. "The prompt fix would work with a better model." No. The transition executes after the prompt was consumed; no model quality changes the order of operations.
2. "Cancel-on-Interrupt is enough." It closes the interrupted case and leaves the uninterrupted skip open; and it depends on winning a race every time.
3. "Zero violations means the guard is correct." It means no violation is reachable within the bound and the model. Have students say the bound.
4. "The guard is a filter on the utterance." It is the absence of the utterance from the decision. The strongest guarantee came from removing a parameter.
5. "This is a voice problem." It is a property of any agent whose tool calls can commit before the turn that authorised them is grounded: chat agents with a stop button, streaming tool arguments, multi-agent handoffs.

## Discussion questions

- Where in a system you have built is a side effect committed before the input that justified it is final?
- What would a transcript audit need in order to see this fault? What would the audit have to read instead of the transcript?
- If the facts record can only be written from observed events, who decides what counts as an event? What happens to the guarantee if that decision is delegated to the model?

## Assessment ideas

- Short: given a log and a final phase, say whether a phantom transition occurred and which invariant it violated.
- Longer: take any phase-gated agent tutorial from the web, identify where the transition commits relative to the turn, and classify its fix (if any) as one of the three patterns.

## Materials in this folder

- `interrupted_turns_advance_state.ipynb`: the notebook, executed, outputs included.
- `ANSWER-KEY.md`: a worked answer to the exercise with the three deliberate breaks.
- `requirements.txt`: pytest, hypothesis, jupyter.
- `LICENSE-MATERIALS`: CC BY 4.0 for the prose and notebook. The code in `src/` and `tests/` is MIT.

Reference implementation and twenty-four tests: https://github.com/Kazemkhani/phantom-transition
