"""Adopting the phase guard in a LiveKit Agents worker.

Read against an installed `livekit-agents` 1.7.1. Every API name in this file
was taken from that tree, and the citations in the comments are file and line
numbers inside it.

This file is illustrative rather than executable: it deliberately does not
choose speech or language model providers, because the guard has nothing to do
with either and naming them would date the example. Fill in the three NOT
PROVIDED slots in `entrypoint` with whatever your worker already uses and it
runs.

------------------------------------------------------------------------------
The five lines
------------------------------------------------------------------------------

    from phantom_transition import Phase
    from phantom_transition.livekit import guard_session, guarded_transition

    guard_session(session)                                    # 1

    @function_tool()                                          # 2
    @guarded_transition(Phase.DISCOVERY)                      # 3
    async def move_to_discovery(context: RunContext) -> str:  # 4
        return "Thanks. What brought you in today?"           # 5

Line 1 attaches a `FactsRecorder` to the session. It subscribes to
`speech_created` and `user_input_transcribed` and writes the guard's facts from
what it observes. Lines 2 to 5 are an ordinary function tool with one extra
decorator between `@function_tool()` and the function.

`@guarded_transition` goes *under* `@function_tool()`, closest to the function.
Above it, `function_tool` would be handed a `FunctionTool` object rather than
your function, and would build the tool's name, description and schema from the
wrong callable (`llm/tool_context.py:380-381`, `llm/utils.py:503-504`).
"""

from __future__ import annotations

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)

from phantom_transition import Phase
from phantom_transition.livekit import guard_session, guarded_transition, recorder_for


# -----------------------------------------------------------------------------
# The tools
# -----------------------------------------------------------------------------
#
# Each one advances one phase. The body says what the agent should say next and
# nothing else: it must not write the phase, because the adapter commits that
# after the body returns and after one last check that the turn was not
# interrupted while the body ran. A body that moved the phase itself would
# reopen exactly the window the guard closes.


class QualificationAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are answering an inbound enquiry. Greet the caller, find out what "
                "they need, describe how you can help, and then arrange a follow-up. "
                "Move between those stages with the tools you have been given. If a "
                "tool tells you the stage did not change, keep talking in the stage you "
                "are in and try again later."
            )
        )

    @function_tool()
    @guarded_transition(Phase.DISCOVERY)
    async def move_to_discovery(self, context: RunContext) -> str:
        """Move on to finding out what the caller needs, once you have greeted them."""
        return "Thanks for that. Can I ask what prompted you to get in touch today?"

    @function_tool()
    @guarded_transition(Phase.PITCH)
    async def move_to_pitch(self, context: RunContext) -> str:
        """Move on to describing how you can help, once you understand the need."""
        return "That is helpful. Let me tell you how we would approach it."

    @function_tool()
    @guarded_transition(Phase.CLOSE)
    async def move_to_close(self, context: RunContext) -> str:
        """Move on to arranging the follow-up, once you have described how you help."""
        return "Shall we put some time in the diary to go through it properly?"


# -----------------------------------------------------------------------------
# The worker
# -----------------------------------------------------------------------------


async def entrypoint(ctx: JobContext) -> None:
    session: AgentSession = AgentSession(
        # stt=...,  NOT PROVIDED: your speech-to-text
        # llm=...,  NOT PROVIDED: your language model
        # tts=...,  NOT PROVIDED: your text-to-speech
    )

    # The whole adoption. `guard_session` returns the recorder if you want to
    # read it; you do not have to hold on to it, because `guarded_transition`
    # finds it again through `RunContext.session` (events.py:72-74), and so can
    # you, with `recorder_for(session)`.
    guard_session(session)

    async def summarise() -> None:
        # Worth logging at the end of every call. An empty observation log means
        # no session event ever reached the recorder, so the guard has been
        # running on an empty facts record and refusing everything. That failure
        # is loud here by design; the alternative is the one described below.
        recorder = recorder_for(session)
        if recorder is None:
            print("guard: no recorder attached")
            return
        print(
            "guard: call ended in "
            + recorder.phase.name
            + " after "
            + str(len(recorder.observations))
            + " observations, facts "
            + str(recorder.facts)
        )

    ctx.add_shutdown_callback(summarise)

    await session.start(agent=QualificationAgent(), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


# =============================================================================
# The production hook this replaces
# =============================================================================
#
# A real deployment of this shape (four phase agents, three handoff tools, a
# barge-in on nearly every turn) had already identified the fault and written a
# correction for it. Anonymised, with the phase names normalised, it was:
#
#     phase_transition_tools = {"move_to_discovery", "move_to_pitch", "move_to_close"}
#
#     @session.on("function_tools_executed")
#     def _on_tools_executed(ev: FunctionToolsExecutedEvent):
#         if not ev.has_agent_handoff:
#             return
#         for fnc_call, _output in ev.zipped():
#             if (
#                 fnc_call.name in phase_transition_tools
#                 and call_state.interrupted_during_transition
#             ):
#                 ev.cancel_agent_handoff()
#                 call_state.interrupted_during_transition = False
#                 return
#
# Every API in it is real: `FunctionToolsExecutedEvent` (events.py:419-458),
# `.has_agent_handoff` (:449-451), `.zipped()` (:434-436) and
# `.cancel_agent_handoff()` (:442-443) all exist and all do what the names say.
# It reads like a finished fix. It never fired once, for two independent
# reasons, and the second is the interesting one.
#
# Reason one, the ordinary one. `interrupted_during_transition` is never
# assigned `True` anywhere in that codebase except in a test fixture. Five
# handlers are registered on the session and none of them sets it. The trigger
# was designed, the flag was declared, the telemetry was wired, and the wire
# from the interruption to the flag was never run. A code review sees a fix; a
# grep for the assignment sees dead code.
#
# Reason two, which holds even after you fix reason one. The handler's first
# line is `if not ev.has_agent_handoff: return`. `has_agent_handoff` reads the
# private `_handoff_required` (events.py:449-451), and `_handoff_required` is
# set to `True` in exactly one place: `agent_activity.py:3676`, inside the
# branch taken when the speech was **not** interrupted. The interrupted branch
# is `agent_activity.py:3610-3631`; it builds its own event at :3624-3627,
# leaving `_handoff_required` at its declared default of `False`
# (events.py:432), and returns at :3631. So on precisely the interrupted turn
# this handler exists to catch, `has_agent_handoff` is `False` and the handler
# returns on its first line. And `cancel_agent_handoff()` would have nothing to
# cancel in any case, because the interrupted branch returns before reaching
# `session.update_agent(...)` at :3682.
#
# The lesson is not that someone wired it up wrong. It is that the shape is
# wrong. A cancel-on-interrupt correction has to run after the transition has
# committed, which means it has to be reachable on the interrupted path, and on
# the interrupted path the framework has already decided what it is telling you.
# You are trying to win a race that the runtime has finished running.
#
# The rewrite is not a better version of that handler. It is the same intent
# moved to a place where there is no race:
#
#     recorder = guard_session(session)
#
#     @function_tool()
#     @guarded_transition(Phase.DISCOVERY)
#     async def move_to_discovery(self, context: RunContext) -> str:
#         return "Thanks. What brought you in today?"
#
# `move_to_discovery` in that deployment had no runtime guard at all; the two
# tools that did had threshold checks on prior counters and on the previous
# phase string, which is a different property from "the evidence for entering
# this phase was recorded". Under the adapter all three are gated the same way,
# and the gate is not reachable from the conversation.
#
# What you give up: nothing in the transcript, and one extra decorator per
# transition tool. What you get: a transition can be proposed by anything at
# all, at any moment in the turn, and still cannot commit unless the facts for
# its destination were written by an event that actually happened.
#
# -----------------------------------------------------------------------------
# On context.disallow_interruptions()
# -----------------------------------------------------------------------------
#
# `RunContext.disallow_interruptions()` (events.py:88-97) sets
# `SpeechHandle.allow_interruptions = False` (speech_handle.py:116-135), so the
# turn carrying this tool call can no longer be interrupted while it runs. The
# LiveKit documentation recommends it for mutating tools, and for many mutating
# tools it is the right answer.
#
# It is not this one, for three reasons.
#
# 1. It buys state safety by spending barge-in, on every transition, for every
#    caller. In a real-time call that is a naturalness cost the caller hears.
# 2. It is a per-tool opt-in. It protects the tools somebody remembered to
#    annotate, and does not survive the next tool being added by someone who
#    did not read this comment.
# 3. It answers a different question. It prevents cancellation *during
#    execution*. It says nothing about whether the destination phase's entry
#    evidence was ever recorded, and it raises `RuntimeError` if the handle is
#    already interrupted, which is the case that started all this.
#
# The two compose, and there is no reason not to use both on a tool that also
# talks to a payment API. `guarded_transition` decides admission; whether the
# turn can be interrupted while the body runs is a separate choice.
