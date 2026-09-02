"""A framework-free text agent with a stop button.

No voice, no framework, no model. An asyncio agent streams a scripted reply
token by token to a sink, executes tool calls the moment the scripted model
emits them (speculative execution, the default in every runtime measured), and
exposes a stop button that cancels the turn. The experiment presses the stop
button at a seeded offset and reads the phase afterwards.

Three commit policies isolate what actually decides the outcome:

speculative
    The tool runs as its own task the moment it is emitted; the stop button
    cancels the turn task only. This mirrors the runtimes in the matrix.
cancel-tool
    The stop button also cancels the tool task. Whether the mutation survives
    then depends on whether the tool had already passed its mutation point.
staged
    The tool writes to a staging slot; the mutation is applied only when the
    turn completes without being stopped. A stopped turn never commits.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

from harness import (
    INITIAL_PHASE,
    REPLY_TEXT,
    TARGET_PHASE,
    TOOL_NAME,
    Config,
    RunRecord,
    Schedule,
)
from harness.protocol import REPLY_AUDIO_SECONDS

Policy = Literal["speculative", "cancel-tool", "staged"]
POLICIES: tuple[Policy, ...] = ("speculative", "cancel-tool", "staged")


@dataclass
class ToolCallChunk:
    name: str
    arguments: dict


@dataclass
class TextChunk:
    text: str


Chunk = ToolCallChunk | TextChunk


class ScriptedLLM:
    """Emits one tool call at a fixed offset and the reply text at a fixed rate."""

    def __init__(self, *, tool_emit_offset_s: float, time_scale: float) -> None:
        self._tool_emit_offset_s = tool_emit_offset_s
        self._scale = time_scale
        tokens = REPLY_TEXT.split(" ")
        self._tokens = [t + " " for t in tokens]
        self._interval = REPLY_AUDIO_SECONDS / len(self._tokens)

    async def stream(self) -> AsyncIterator[Chunk]:
        emitted = False
        elapsed = 0.0
        if self._tool_emit_offset_s <= 0.0:
            emitted = True
            yield ToolCallChunk(TOOL_NAME, {"target": TARGET_PHASE})
        for tok in self._tokens:
            if not emitted and elapsed >= self._tool_emit_offset_s:
                emitted = True
                yield ToolCallChunk(TOOL_NAME, {"target": TARGET_PHASE})
            yield TextChunk(tok)
            await asyncio.sleep(self._interval * self._scale)
            elapsed += self._interval
        if not emitted:
            yield ToolCallChunk(TOOL_NAME, {"target": TARGET_PHASE})


@dataclass
class SessionState:
    phase: str = INITIAL_PHASE
    staged_phase: str | None = None
    spoken: list[str] = field(default_factory=list)
    tool_emitted: bool = False
    tool_started: bool = False
    tool_finished: bool = False
    tool_cancelled: bool = False
    stop_pressed: bool = False
    stop_honoured: bool = False
    interruptions_allowed: bool = True
    active_agent: str = "greeting"
    t0: float = 0.0
    t_tool_started: float | None = None
    t_tool_finished: float | None = None
    t_stop: float | None = None


class TextAgent:
    def __init__(
        self,
        *,
        config: Config,
        schedule: Schedule,
        policy: Policy,
        time_scale: float = 1.0,
    ) -> None:
        self.config = config
        self.schedule = schedule
        self.policy = policy
        self.scale = time_scale
        self.state = SessionState()
        self._turn_task: asyncio.Task[None] | None = None
        self._tool_tasks: list[asyncio.Task[None]] = []

    # -- the tool ----------------------------------------------------------
    async def advance_phase(self, target: str) -> str:
        st = self.state
        st.tool_started = True
        st.t_tool_started = time.perf_counter() - st.t0
        if self.config.disallow_interruptions:
            # The runtime equivalent of RunContext.disallow_interruptions():
            # from here the stop button is ignored for the rest of the turn.
            st.interruptions_allowed = False
        try:
            if self.schedule.tool_delay_s > 0:
                await asyncio.sleep(self.schedule.tool_delay_s * self.scale)
            if self.policy == "staged":
                st.staged_phase = target
            else:
                st.phase = target
                if self.config.handoff:
                    # A framework-free agent has no handoff primitive beyond
                    # another field on the same state; it shares the mutation's fate.
                    st.active_agent = target.lower()
            st.tool_finished = True
            st.t_tool_finished = time.perf_counter() - st.t0
            return f"phase is now {target}"
        except asyncio.CancelledError:
            st.tool_cancelled = True
            raise

    # -- the turn ----------------------------------------------------------
    async def _turn(self, llm: ScriptedLLM) -> None:
        st = self.state
        async for chunk in llm.stream():
            if isinstance(chunk, ToolCallChunk):
                st.tool_emitted = True
                task = asyncio.create_task(self.advance_phase(**chunk.arguments))
                self._tool_tasks.append(task)
            else:
                st.spoken.append(chunk.text)
        # The reply finished. Wait for tools, then commit staged work.
        if self._tool_tasks:
            await asyncio.gather(*self._tool_tasks, return_exceptions=True)
        if self.policy == "staged" and st.staged_phase is not None:
            st.phase = st.staged_phase
            if self.config.handoff:
                st.active_agent = st.staged_phase.lower()
            st.staged_phase = None

    def press_stop(self) -> None:
        st = self.state
        st.stop_pressed = True
        st.t_stop = time.perf_counter() - st.t0
        if not st.interruptions_allowed:
            return
        st.stop_honoured = True
        if self._turn_task is not None:
            self._turn_task.cancel()
        if self.policy == "cancel-tool":
            for t in self._tool_tasks:
                t.cancel()

    async def run(self) -> RunRecord:
        st = self.state
        st.t0 = time.perf_counter()
        llm = ScriptedLLM(
            tool_emit_offset_s=self.config.tool_emit_offset_s, time_scale=self.scale
        )
        self._turn_task = asyncio.create_task(self._turn(llm))
        loop = asyncio.get_running_loop()
        loop.call_later(self.schedule.barge_in_offset_s * self.scale, self.press_stop)
        try:
            await self._turn_task
        except asyncio.CancelledError:
            pass
        # Let any tool task that was left running finish, as every runtime in the
        # matrix does, so the final phase is the settled one.
        if self._tool_tasks:
            await asyncio.gather(*self._tool_tasks, return_exceptions=True)
        wall = time.perf_counter() - st.t0
        return RunRecord(
            runtime="text-agent (asyncio, no framework)",
            runtime_version=self.policy,
            config=self.config.name,
            run_index=self.schedule.run_index,
            seed=self.schedule.seed,
            barge_in_offset_s=self.schedule.barge_in_offset_s,
            tool_delay_s=self.schedule.tool_delay_s,
            phase_final=st.phase,
            tool_emitted=st.tool_emitted,
            tool_started=st.tool_started,
            tool_finished=st.tool_finished,
            tool_cancelled=st.tool_cancelled,
            speech_interrupted=st.stop_honoured,
            tool_record_in_context=None,
            tools_executed_event=None,
            handoff_applied=(st.active_agent != "greeting") if self.config.handoff else None,
            playback_seconds=(len(st.spoken) * llm._interval),
            t_tool_started_s=(st.t_tool_started / self.scale if st.t_tool_started else None),
            t_tool_finished_s=(st.t_tool_finished / self.scale if st.t_tool_finished else None),
            t_interrupt_s=(st.t_stop / self.scale if st.t_stop is not None else None),
            wall_time_s=wall,
            notes=(
                "stop ignored: interruptions disallowed by the tool"
                if st.stop_pressed and not st.stop_honoured
                else ""
            ),
        )


async def run_one(
    config: Config, schedule: Schedule, *, policy: Policy, time_scale: float = 1.0
) -> RunRecord:
    return await TextAgent(
        config=config, schedule=schedule, policy=policy, time_scale=time_scale
    ).run()


MECHANISM = {
    "speculative": (
        "the tool runs as its own asyncio task the instant it is emitted; the stop button "
        "cancels the turn task only, so a mutation that happened, or happens later in a task "
        "nobody cancelled, stands. A call not yet emitted when the stop lands is never made"
    ),
    "cancel-tool": (
        "the stop button cancels the tool task as well; CancelledError is delivered at the "
        "tool's next await, so a mutation before that await stands and one after it does not"
    ),
    "staged": (
        "the tool writes to a staging slot and the turn commits it only on completing "
        "un-stopped; a stopped turn discards the slot, whatever the timing"
    ),
}
