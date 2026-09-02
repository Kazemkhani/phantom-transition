# Answer key: the exercise in Section 8

The exercise asks for a ninth move (`"stale"`), a fifth invariant (the log agrees with the session), a re-run of both instruments, and then a deliberate break. The code below is one correct answer; others are possible, and the discussion at the end is the part that matters.

## 1. The stale move

A stale tool call is one where the model reasoned from a previous view of the session and proposes `target` relative to a `current` that is no longer current. The guard already refuses anything that is not exactly one step ahead of the real `current`, so the design question is only what the *session* should do when the call arrives carrying a stale origin. One defensible answer: ignore the carried origin entirely and evaluate the target against the session's own phase, which is what a guard that reads only runtime state does anyway.

```python
class GuardedSession:
    def __init__(self, facts=Facts(), guard=None):
        self.phase = Phase.GREETING; self.facts = facts
        self.guard = guard or PhaseGuard(); self.log = []
    def turn(self, target, interrupted=False, stale_origin=None, **forged):
        # stale_origin is what the model believed the phase was. The guard never sees it.
        ok, why = self.guard.check(self.phase, target, self.facts)
        if not ok:
            self.log.append(("refused", why)); return None
        if interrupted:
            self.log.append(("rolled back", target.name)); return None
        self.phase = target; self.log.append(("advanced", target.name))
        return f"spoken: entering {target.name}"
```

The point students should notice: adding the move required no change to `PhaseGuard.check`, because the stale origin is one more thing the guard's signature does not admit.

## 2. The fifth invariant

"The phase recorded in the log always equals the phase the session is in." Reconstruct the phase from the log and compare.

```python
def phase_from_log(log):
    phase = Phase.GREETING
    for kind, detail in log:
        if kind == "advanced":
            phase = Phase[detail]
    return phase

INVARIANTS.append("log and session agree on the phase")

def violations_after(before, after, interrupted, facts, log=None):
    found = []
    if after < before:                           found.append(INVARIANTS[0])
    if after - before > 1:                       found.append(INVARIANTS[1])
    if interrupted and after != before:          found.append(INVARIANTS[2])
    if after != before and not ENTRY[after][1](facts):
                                                 found.append(INVARIANTS[3])
    if log is not None and phase_from_log(log) != after:
                                                 found.append(INVARIANTS[4])
    return found
```

## 3. The exhaustive run

```python
def exhaustive_four_turn(guard=None):
    targets = [Phase.DISCOVERY, Phase.PITCH, Phase.CLOSE]
    conditions = ["clean", "interrupted", "forged", "stale"]     # 3 x 4 = 12 moves
    alphabet = [(t, c) for t in targets for c in conditions]
    checked = violations = 0
    for seq in product(alphabet, repeat=4):                       # 12^4 = 20,736
        s = GuardedSession(Facts(True, 2, True), guard=guard)
        for target, cond in seq:
            interrupted = (cond == "interrupted")
            forged = {"authorised": True, "override": "yes"} if cond == "forged" else {}
            stale = Phase.GREETING if cond == "stale" else None
            before = s.phase
            s.turn(target, interrupted=interrupted, stale_origin=stale, **forged)
            violations += len(violations_after(before, s.phase, interrupted, s.facts, s.log))
        checked += 1
    return checked, violations
```

Expected output: `sequences checked : 20,736`, `violations found  : 0`.

## 4. The Hypothesis run

Add `stale=st.booleans()` to the rule, pass `stale_origin=Phase.GREETING if stale else None`, and pass `self.s.log` into `violations_after`. Expected: no violation over 300 examples.

## 5. Breaking it on purpose

Three breaks worth trying, in increasing subtlety:

1. Delete the `target - current != 1` check. Both instruments find it immediately; the exhaustive run reports thousands of violations, Hypothesis shrinks to a single move `CLOSE`.
2. Make the guard return `True` for `PITCH` when `discovery_answers >= 1` instead of `>= 2`, with facts set to one answer. Both find it; Hypothesis shrinks to `DISCOVERY, PITCH`.
3. Record the phase in the log before the guard runs (move `self.log.append(("advanced", ...))` above the check). Only the fifth invariant catches this, which is the lesson: an invariant over the *record* catches faults an invariant over the *state* cannot, and a system whose record disagrees with its state is exactly the phantom transition seen from the other side.

## Discussion points for the facilitator

- Which instrument told you *why* faster? Hypothesis, because it hands back a minimal sequence; the exhaustive run hands back a count.
- Which instrument would you trust for a release gate? The exhaustive run, within its bound, because it is a proof over the model. Say the bound out loud.
- What did the stale move teach about the signature? That a decision function which cannot read a value does not need a rule about that value.
