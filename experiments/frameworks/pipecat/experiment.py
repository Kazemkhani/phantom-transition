"""Drive a real Pipecat pipeline through the phantom-transition scenario.

Everything that matters here is Pipecat's own code: the pipeline worker, the
frame queues, `FrameProcessor._start_interruption` (which cancels a
processor's in-flight process task when an `InterruptionFrame` arrives) and
`LLMService._handle_interruptions` (which cancels running function-call tasks
registered with `cancel_on_interruption=True`, the default).

The scripted LLM subclasses `LLMService`: on `LLMRunFrame` it streams the
reply text token by token over REPLY_AUDIO_SECONDS inside its process task
(the position a provider's streaming inference occupies) and dispatches the
tool call through the framework's own `run_function_calls`. The barge-in is an
`InterruptionFrame` queued at the seeded offset after the first streamed
token, which is exactly what a Pipecat input transport broadcasts when VAD
detects user speech while interruptions are enabled.

Configuration mapping:

- sync-tool / inflight-tool / late-tool: framework defaults
  (cancel_on_interruption=True).
- disallow-interruptions: Pipecat has no per-tool barge-in opt-out; its
  nearest per-tool safety knob is `register_function(...,
  cancel_on_interruption=False)`, which protects the call from cancellation
  but does not suppress the barge-in. That is what this configuration
  measures here, and the cell records barge-in as not suppressed.
- handoff-tool: not runnable; core Pipecat has no agent-handoff primitive.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from pipecat.frames.frames import (
    Frame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
)
from pipecat.frames.frames import FunctionCallFromLLM
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams, LLMService
from pipecat.workers.runner import WorkerRunner

import importlib.metadata

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

PIPECAT_VERSION = importlib.metadata.version("pipecat-ai")


@dataclass
class Observed:
    phase: str = INITIAL_PHASE
    tool_started: bool = False
    tool_finished: bool = False
    tool_cancelled: bool = False
    tool_emitted: bool = False
    result_frames: list[Frame] = field(default_factory=list)
    first_token_at: float | None = None
    t_tool_started: float | None = None
    t_tool_finished: float | None = None
    stream_cancelled: bool = False
    notes: list[str] = field(default_factory=list)


class ScriptedLLM(LLMService):
    """Streams the scripted reply and dispatches the tool call at its offset."""

    def __init__(self, *, config: Config, schedule: Schedule, obs: Observed) -> None:
        super().__init__(run_in_parallel=True)
        self._config = config
        self._schedule = schedule
        self._obs = obs
        tokens = [t + " " for t in REPLY_TEXT.split(" ")]
        self._tokens = tokens
        self._interval = REPLY_AUDIO_SECONDS / len(tokens)

        async def advance_phase(params: FunctionCallParams) -> None:
            obs.tool_started = True
            obs.t_tool_started = time.perf_counter()
            try:
                if schedule.tool_delay_s > 0:
                    await asyncio.sleep(schedule.tool_delay_s)
                obs.phase = params.arguments.get("target", TARGET_PHASE)
                obs.tool_finished = True
                obs.t_tool_finished = time.perf_counter()
                await params.result_callback(f"phase is now {obs.phase}")
            except asyncio.CancelledError:
                obs.tool_cancelled = True
                raise

        self.register_function(
            TOOL_NAME,
            advance_phase,
            cancel_on_interruption=not config.disallow_interruptions,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMRunFrame):
            await self._stream_reply()
        else:
            await self.push_frame(frame, direction)

    async def _stream_reply(self) -> None:
        obs = self._obs
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            emitted = False
            elapsed = 0.0
            if self._config.tool_emit_offset_s <= 0.0:
                emitted = True
                await self._dispatch_tool()
            for tok in self._tokens:
                if not emitted and elapsed >= self._config.tool_emit_offset_s:
                    emitted = True
                    await self._dispatch_tool()
                if obs.first_token_at is None:
                    obs.first_token_at = time.perf_counter()
                await self.push_frame(LLMTextFrame(tok))
                await asyncio.sleep(self._interval)
                elapsed += self._interval
            if not emitted:
                await self._dispatch_tool()
        except asyncio.CancelledError:
            obs.stream_cancelled = True
            raise
        finally:
            await self.push_frame(LLMFullResponseEndFrame())

    async def _dispatch_tool(self) -> None:
        self._obs.tool_emitted = True
        await self.run_function_calls(
            [
                FunctionCallFromLLM(
                    function_name=TOOL_NAME,
                    tool_call_id="call_advance_1",
                    arguments={"target": TARGET_PHASE},
                    context=None,
                )
            ]
        )


class Recorder(FrameProcessor):
    """Downstream sink that timestamps the frames the run cares about."""

    def __init__(self, obs: Observed) -> None:
        super().__init__(enable_direct_mode=True)
        self._obs = obs
        self.saw_interruption = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, FunctionCallResultFrame):
            self._obs.result_frames.append(frame)
        if isinstance(frame, InterruptionFrame):
            self.saw_interruption.set()
        await self.push_frame(frame, direction)


async def run_one(config: Config, schedule: Schedule, *, settle_s: float = 0.5) -> RunRecord:
    if config.handoff:
        raise RuntimeError("core Pipecat has no agent-handoff primitive")

    t0 = time.perf_counter()
    obs = Observed()
    llm = ScriptedLLM(config=config, schedule=schedule, obs=obs)
    recorder = Recorder(obs)
    pipeline = Pipeline([llm, recorder])
    worker = PipelineWorker(
        pipeline,
        cancel_on_idle_timeout=False,
        params=PipelineParams(),
    )

    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def _on_started(worker: PipelineWorker, frame: Frame) -> None:
        started.set()

    async def drive() -> None:
        await asyncio.wait_for(started.wait(), timeout=5.0)
        await worker.queue_frame(LLMRunFrame())
        # anchor the barge-in on the first streamed token, like the other runtimes
        while obs.first_token_at is None:
            await asyncio.sleep(0.005)
        delay = schedule.barge_in_offset_s - (time.perf_counter() - obs.first_token_at)
        if delay > 0:
            await asyncio.sleep(delay)
        interrupt_at = time.perf_counter()
        obs.notes.append(f"interruption queued at +{interrupt_at - obs.first_token_at:.3f}s")
        await worker.queue_frame(InterruptionFrame())
        # let the interruption propagate, the tool settle, and any protected
        # call run its course before tearing the pipeline down
        wait_for = settle_s + (
            schedule.tool_delay_s if config.disallow_interruptions else 0.0
        )
        await asyncio.sleep(wait_for + 0.5)
        await worker.cancel()

    runner = WorkerRunner()
    await runner.add_workers(worker)
    await asyncio.gather(runner.run(), drive())

    interrupted = obs.stream_cancelled or recorder.saw_interruption.is_set()
    rel = obs.first_token_at or t0
    return RunRecord(
        runtime="Pipecat",
        runtime_version=PIPECAT_VERSION,
        config=config.name,
        run_index=schedule.run_index,
        seed=schedule.seed,
        barge_in_offset_s=schedule.barge_in_offset_s,
        tool_delay_s=schedule.tool_delay_s,
        phase_final=obs.phase,
        tool_emitted=obs.tool_emitted,
        tool_started=obs.tool_started,
        tool_finished=obs.tool_finished,
        tool_cancelled=obs.tool_cancelled,
        speech_interrupted=interrupted,
        tool_record_in_context=bool(obs.result_frames),
        tools_executed_event=bool(obs.result_frames),
        handoff_applied=None,
        playback_seconds=None,
        t_tool_started_s=(obs.t_tool_started - rel if obs.t_tool_started else None),
        t_tool_finished_s=(obs.t_tool_finished - rel if obs.t_tool_finished else None),
        t_interrupt_s=None,
        wall_time_s=round(time.perf_counter() - t0, 3),
        notes="; ".join(obs.notes)
        + (
            "; per-tool opt-out here is cancel_on_interruption=False, which does not "
            "suppress the barge-in"
            if config.disallow_interruptions
            else ""
        ),
    )
