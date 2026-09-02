---------------------------- MODULE PhaseGuard ----------------------------
(***************************************************************************)
(* A bounded model of the phase-gated session in                           *)
(* src/phantom_transition/session.py.                                      *)
(*                                                                         *)
(* One turn of the model is one call to Session.handle_turn. The turn's    *)
(* tool calls execute in order against the running phase, each one         *)
(* admitted or refused by PhaseGuard.check; an interrupted turn then       *)
(* discards its speech and (if CancelOnInterrupt) unwinds the handoffs it  *)
(* executed, newest first; a clean turn records facts from what the        *)
(* runtime observed.                                                       *)
(*                                                                         *)
(* The utterance is not a variable of this specification. Nothing in the   *)
(* guard, the record step or the moves reads it, which is the design       *)
(* claim of the paper made structural: there is nothing for an attacker    *)
(* to phrase. The forged call (move 9) is modelled by its only effect at   *)
(* the tool boundary, a target that is not a phase; the extra arguments    *)
(* it carries (authorised, override, force) are not parameters of the      *)
(* guard and therefore have no representation here either.                 *)
(*                                                                         *)
(* The three CONSTANTS mirror the three switches of Session.__init__.       *)
(* PhaseGuard.cfg checks the guarded session; PhaseGuard_unguarded.cfg     *)
(* checks the reproduction of the fault (preemptive generation on, no      *)
(* cancellation, no guard) and TLC produces the violating trace.           *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS
    PreemptiveGeneration,  \* Session(preemptive_generation=...)
    CancelOnInterrupt,     \* Session(cancel_handoff_on_interrupt=...)
    GuardEnabled,          \* Session(guard_enabled=...)
    MaxTurns,              \* the bound on the number of turns explored
    Forged                 \* a model value: a target that is not a Phase

ASSUME PreemptiveGeneration \in BOOLEAN
ASSUME CancelOnInterrupt \in BOOLEAN
ASSUME GuardEnabled \in BOOLEAN
ASSUME MaxTurns \in Nat

-----------------------------------------------------------------------------
(* Phase, an IntEnum in the reference model: the only order they may be    *)
(* traversed is numeric.                                                   *)
GREETING  == 0
DISCOVERY == 1
PITCH     == 2
CLOSE     == 3
Phases    == {GREETING, DISCOVERY, PITCH, CLOSE}

(* Facts: written by the runtime from observed events, never from          *)
(* utterances. A frozen dataclass in the reference model.                  *)
FactRecords == [greeting_delivered: BOOLEAN,
                discovery_answers: 0..MaxTurns,
                pitch_delivered: BOOLEAN]

InitialFacts == [greeting_delivered |-> FALSE,
                 discovery_answers  |-> 0,
                 pitch_delivered    |-> FALSE]

(* ENTRY_CONDITIONS. GREETING has no entry condition, as in the Python     *)
(* dict, where .get(Phase.GREETING) is None and the check is skipped.      *)
Entry(p, f) ==
    CASE p = DISCOVERY -> f.greeting_delivered
      [] p = PITCH     -> f.discovery_answers >= 2
      [] p = CLOSE     -> f.pitch_delivered
      [] OTHER         -> TRUE

(* PhaseGuard.check(current, target, facts) -> (allowed, reason).          *)
(* The five refusals, in the order the reference model tests them. The     *)
(* utterance is not a parameter.                                           *)
Admit(current, target, f) ==
    /\ target \in Phases          \* isinstance(target, Phase)
    /\ target /= current          \* "already in that phase"
    /\ target > current           \* "phase progression is forward only"
    /\ target = current + 1       \* "cannot skip from X to Y"
    /\ Entry(target, f)           \* "entry conditions for Y not met"

(* Session._execute without the guard still refuses a target that is not   *)
(* a Phase; it refuses nothing else.                                       *)
Executes(current, target, f) ==
    IF GuardEnabled THEN Admit(current, target, f) ELSE target \in Phases

-----------------------------------------------------------------------------
(* The nine-move alphabet of test_12 in tests/test_guard.py. A move is one  *)
(* caller turn: whether it was interrupted, whether it answered a          *)
(* discovery question (an observed event, not a parse of the utterance),   *)
(* and the sequence of advance_phase targets the model issued on it.       *)
(* Moves 3 and 6 differ only in the utterance (3 carries a prompt          *)
(* injection); they are kept distinct so the alphabet is the paper's, and  *)
(* their effects are identical because the utterance is unread.            *)
Move(id, interrupted, answers, calls) ==
    [id |-> id, interrupted |-> interrupted, answers |-> answers, calls |-> calls]

Moves == {
    Move(1, FALSE, FALSE, << >>),                 \* "hello"
    Move(2, FALSE, TRUE,  << >>),                 \* "about ten staff", answers a discovery question
    Move(3, FALSE, FALSE, << CLOSE >>),           \* prompt injection, advance(CLOSE)
    Move(4, FALSE, FALSE, << DISCOVERY >>),       \* "ok", advance(DISCOVERY)
    Move(5, FALSE, FALSE, << PITCH >>),           \* "ok", advance(PITCH)
    Move(6, FALSE, FALSE, << CLOSE >>),           \* "ok", advance(CLOSE)
    Move(7, TRUE,  FALSE, << DISCOVERY >>),       \* barge-in, advance(DISCOVERY)
    Move(8, TRUE,  FALSE, << PITCH, CLOSE >>),    \* barge-in, advance(PITCH), advance(CLOSE)
    Move(9, FALSE, FALSE, << Forged >>)           \* advance(target="CLOSE", authorised=True)
}

NoMove == Move(0, FALSE, FALSE, << >>)

-----------------------------------------------------------------------------
VARIABLES
    phase,      \* Session.phase
    facts,      \* Session.facts
    turn,       \* turns handled so far; the bound is MaxTurns
    events,     \* the event kinds the last turn emitted (Session.events, last turn)
    prevPhase,  \* history: phase at the start of the last turn
    prevFacts,  \* history: facts at the start of the last turn
    lastMove    \* history: the move the last turn took

vars == << phase, facts, turn, events, prevPhase, prevFacts, lastMove >>

-----------------------------------------------------------------------------
(* Execute a turn's tool calls in order (Session._execute, once per call).  *)
(* Returns the phase after the calls, the handoffs that executed (each     *)
(* with the phase it advanced from, as Event.previous), and the events.    *)
RECURSIVE Run(_, _, _, _, _, _)
Run(calls, i, p, f, executed, evs) ==
    IF i > Len(calls)
    THEN [phase |-> p, executed |-> executed, events |-> evs]
    ELSE LET t == calls[i]
         IN IF Executes(p, t, f)
            THEN Run(calls, i + 1, t, f,
                     Append(executed, [previous |-> p, target |-> t]),
                     Append(evs, "handoff_executed"))
            ELSE Run(calls, i + 1, p, f, executed, Append(evs, "handoff_denied"))

(* Session._rollback applied newest first: each step restores the phase    *)
(* the handoff advanced from.                                              *)
RECURSIVE Unwind(_, _, _)
Unwind(executed, k, p) ==
    IF k = 0 THEN p ELSE Unwind(executed, k - 1, executed[k].previous)

(* Session._record: facts are written from what the runtime observed on a  *)
(* clean turn, against the phase the session is in once the turn's calls   *)
(* have run. The utterance does not appear.                                *)
Record(p, f, answers) ==
    CASE p = GREETING              -> [f EXCEPT !.greeting_delivered = TRUE]
      [] p = DISCOVERY /\ answers  -> [f EXCEPT !.discovery_answers = @ + 1]
      [] p = PITCH                 -> [f EXCEPT !.pitch_delivered = TRUE]
      [] OTHER                     -> f

-----------------------------------------------------------------------------
Init ==
    /\ phase = GREETING
    /\ facts = InitialFacts
    /\ turn = 0
    /\ events = << >>
    /\ prevPhase = GREETING
    /\ prevFacts = InitialFacts
    /\ lastMove = NoMove

(* Session.handle_turn(turn, tool_calls). *)
HandleTurn(m) ==
    /\ turn < MaxTurns
    /\ LET commits == PreemptiveGeneration \/ ~m.interrupted
           run == IF commits
                  THEN Run(m.calls, 1, phase, facts, << >>, << >>)
                  ELSE [phase |-> phase, executed |-> << >>, events |-> << >>]
           executed == run.executed
           cancelled == IF CancelOnInterrupt
                        THEN [k \in 1..Len(executed) |-> "handoff_cancelled"]
                        ELSE << >>
       IN IF m.interrupted
          THEN /\ phase' = IF CancelOnInterrupt
                           THEN Unwind(executed, Len(executed), run.phase)
                           ELSE run.phase
               /\ facts' = facts
               /\ events' = run.events \o << "speech_discarded" >> \o cancelled
          ELSE /\ phase' = run.phase
               /\ facts' = Record(run.phase, facts, m.answers)
               /\ events' = Append(run.events, "spoke")
    /\ turn' = turn + 1
    /\ prevPhase' = phase
    /\ prevFacts' = facts
    /\ lastMove' = m

(* The bound reached: the session stutters rather than deadlocking, so TLC *)
(* needs no -deadlock flag.                                                *)
Done == turn = MaxTurns /\ UNCHANGED vars

Next == (\E m \in Moves : HandleTurn(m)) \/ Done

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------
(* Type invariant. *)
TypeOK ==
    /\ phase \in Phases
    /\ facts \in FactRecords
    /\ turn \in 0..MaxTurns
    /\ prevPhase \in Phases
    /\ prevFacts \in FactRecords
    /\ lastMove \in Moves \cup {NoMove}

(* The four invariants of the notebook and of test_12, each stated over    *)
(* the last turn taken (history variables carry the turn's starting        *)
(* state).                                                                 *)

\* 1. Phase never moves backward.
NoBackwardMove == phase >= prevPhase

\* 2. Phase never advances more than one step.
AtMostOneStep == phase <= prevPhase + 1

\* 3. Interrupted turns never change phase.
InterruptedTurnsAreInert == lastMove.interrupted => phase = prevPhase

\* 4. No phase entered without its entry condition satisfied, judged on the
\*    facts as they stood when the transition committed.
EntryConditionRespected == phase /= prevPhase => Entry(phase, prevFacts)

(* The grounded-transition invariant (CONTEXT.md, Section 5, item 1): a    *)
(* transition to phase p may commit only if (a) the turn that proposed it  *)
(* completed without invalidation and (b) the recorded facts satisfied     *)
(* ENTRY(p) at commit time, where facts are written only from observed     *)
(* events. Clause (c) is structural in this module: Record reads no        *)
(* utterance because the module has none.                                  *)
GroundedTransition ==
    phase /= prevPhase => (~lastMove.interrupted /\ Entry(phase, prevFacts))

(* An emergent property of the guarded reference model, reported in        *)
(* formal/README.md: PITCH is only ever entered on a clean turn, and the   *)
(* same turn records pitch_delivered, so the CLOSE gate is never false     *)
(* while the session is in PITCH.                                          *)
PitchRecordsItsDelivery == phase = PITCH => facts.pitch_delivered

=============================================================================
