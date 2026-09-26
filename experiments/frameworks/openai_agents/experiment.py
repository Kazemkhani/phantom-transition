"""Drive the OpenAI Agents SDK through the phantom-transition scenario.

No API key is used: the model is the SDK's own shipped testing double,
`agents.testing.ScriptedModel` (added to the package as a public testing
surface), and the interruption is the SDK's documented stop button,
`RunResultStreaming.cancel()`.

The SDK is a text agent whose tool execution is turn-atomic: the runner
executes a turn's tool calls only after that turn's model stream has
completed, then starts the next turn. The configurations map onto that
honestly:

- sync-tool: turn 1 emits `advance_phase` (the tool mutates instantly), turn 2
  streams the long reply over REPLY_AUDIO_SECONDS; the stop is pressed at the
  seeded offset into the reply stream, mode "immediate".
- inflight-tool: as sync-tool but the tool awaits 3.0 s before mutating; the
  stop at ~1.0 s lands while the tool coroutine is executing.
- late-tool: a single turn streams the reply and carries the tool call; the
  stop lands mid-stream, before the turn completes, so the runner has not yet
  executed the call.
- disallow-interruptions: as sync-tool, but the stop uses the SDK's
  `cancel(mode="after_turn")`, its documented graceful mode ("allows LLM
  response to finish, executes pending tool calls"). The SDK has no per-tool
  opt-out; this run-scoped mode is its nearest equivalent, and the cell
  records whether the reply played to completion (barge-in suppressed).
- handoff-tool: turn 1 emits `advance_phase` and a handoff to the discovery
  agent in the same turn; turn 2 is the new agent's long reply; the stop is
  pressed mid-reply, mode "immediate".
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from agents import Agent, RunConfig, Runner, function_tool
from agents.items import TResponseStreamEvent
from agents.run_context import RunContextWrapper
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from agents.testing.model import ModelCall, _stream_events_for_step

from harness import (
    INITIAL_PHASE,
    REPLY_TEXT,
    TARGET_PHASE,
    TOOL_NAME,
    USER_UTTERANCE,
    Config,
    RunRecord,
    Schedule,
)
from harness.protocol import REPLY_AUDIO_SECONDS

SDK_VERSION = importlib.metadata.version("openai-agents")


@dataclass
class Observed:
    phase: str = INITIAL_PHASE
    tool_started: bool = False
    tool_finished: bool = False
    tool_cancelled: bool = False
    t_tool_started: float | None = None
    t_tool_finished: float | None = None
    first_reply_delta_at: float | None = None
    reply_completed: bool = False
    notes: list[str] = field(default_factory=list)


def _slow_stream_factory(
    step: ModelStep, obs: Observed, *, total_seconds: float, tool_event_offset_s: float | None
) -> Any:
    """Stream a step's synthesised events at a controlled pace.

    Text deltas are spaced so the full stream lasts ``total_seconds``, the
    position a TTS playout or a chat UI's incremental render occupies. When
    ``tool_event_offset_s`` is set, the function-call item's events are held
    until that offset (the late-emit configuration).
    """

    async def factory(call: ModelCall) -> AsyncIterator[TResponseStreamEvent]:
        events = _stream_events_for_step(step, preserve_raw_usage=False)
        deltas = [e for e in events if type(e).__name__ == "ResponseTextDeltaEvent"]
        interval = total_seconds / max(1, len(deltas))
        start = time.perf_counter()
        for event in events:
            name = type(event).__name__
            is_tool_event = "FunctionCall" in name or (
                hasattr(event, "item") and "FunctionToolCall" in type(getattr(event, "item")).__name__
            )
            if tool_event_offset_s is not None and is_tool_event:
                remaining = tool_event_offset_s - (time.perf_counter() - start)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            if name == "ResponseTextDeltaEvent":
                if obs.first_reply_delta_at is None:
                    obs.first_reply_delta_at = time.perf_counter()
                await asyncio.sleep(interval)
            yield event
        obs.reply_completed = True

    return factory


def _reply_step() -> ModelStep:
    return ModelStep(output=[assistant_message(REPLY_TEXT)])


def build(config: Config, schedule: Schedule, obs: Observed):
    @function_tool(name_override=TOOL_NAME)
    async def advance_phase(ctx: RunContextWrapper[Any], target: str) -> str:
        """Advance the call to the next phase.

        Args:
            target: The phase to advance to.
        """
        obs.tool_started = True
        obs.t_tool_started = time.perf_counter()
        try:
            if schedule.tool_delay_s > 0:
                await asyncio.sleep(schedule.tool_delay_s)
            obs.phase = target
            obs.tool_finished = True
            obs.t_tool_finished = time.perf_counter()
            return f"phase is now {target}"
        except asyncio.CancelledError:
            obs.tool_cancelled = True
            raise

    discovery_agent = Agent(
        name="DiscoveryAgent", instructions="You are the discovery-phase assistant."
    )

    agent = Agent(
        name="GreetingAgent",
        instructions="You are a phase-gated assistant.",
        tools=[advance_phase],
        handoffs=[discovery_agent] if config.handoff else [],
    )

    tool_call_item = function_call(TOOL_NAME, {"target": TARGET_PHASE}, call_id="call_advance_1")

    if config.name == "late-tool":
        # one turn: the reply text and the tool call stream together; the call's
        # events are held until tool_emit_offset_s into the stream
        step = ModelStep(output=[assistant_message(REPLY_TEXT), tool_call_item])
        steps = [
            ModelStep.stream(
                _slow_stream_factory(
                    step,
                    obs,
                    total_seconds=REPLY_AUDIO_SECONDS,
                    tool_event_offset_s=config.tool_emit_offset_s,
                )
            ),
            # never reached when the stop lands mid-stream; present so a
            # completed turn (a run that was not stopped in time) can finish
            _reply_step(),
        ]
    elif config.handoff:
        turn1 = [tool_call_item, function_call("transfer_to_discoveryagent", {}, call_id="call_handoff_1")]
        reply = _reply_step()
        steps = [
            ModelStep(output=turn1),
            ModelStep.stream(
                _slow_stream_factory(reply, obs, total_seconds=REPLY_AUDIO_SECONDS, tool_event_offset_s=None)
            ),
        ]
    else:
        reply = _reply_step()
        steps = [
            ModelStep(output=[tool_call_item]),
            ModelStep.stream(
                _slow_stream_factory(reply, obs, total_seconds=REPLY_AUDIO_SECONDS, tool_event_offset_s=None)
            ),
        ]

    model = ScriptedModel(steps)
    return agent, model


async def run_one(config: Config, schedule: Schedule) -> RunRecord:
    t0 = time.perf_counter()
    obs = Observed()
    agent, model = build(config, schedule, obs)
    result = Runner.run_streamed(
        agent, USER_UTTERANCE, run_config=RunConfig(model=model, tracing_disabled=True)
    )

    cancel_mode = "after_turn" if config.disallow_interruptions else "immediate"
    cancelled_at: float | None = None

    async def stopper() -> None:
        nonlocal cancelled_at
        # anchor on the first reply delta when one exists yet; otherwise on the
        # run start (the inflight configuration stops during tool execution,
        # before any reply streams)
        deadline = t0 + 6.0
        while obs.first_reply_delta_at is None and time.perf_counter() < deadline:
            if config.name == "inflight-tool" and obs.tool_started:
                break
            await asyncio.sleep(0.005)
        anchor = obs.first_reply_delta_at or obs.t_tool_started or t0
        delay = anchor + schedule.barge_in_offset_s - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        cancelled_at = time.perf_counter()
        result.cancel(mode=cancel_mode)

    stopper_task = asyncio.create_task(stopper())
    events = []
    try:
        async for event in result.stream_events():
            events.append(type(event).__name__)
    except Exception as e:  # noqa: BLE001
        obs.notes.append(f"stream_events raised {type(e).__name__}: {e}")
    await stopper_task
    # allow an in-flight tool that nobody cancelled to settle
    for _ in range(200):
        if obs.tool_finished or obs.tool_cancelled or not obs.tool_started:
            break
        await asyncio.sleep(0.05)

    new_items = list(result.new_items)
    kinds = [i.type for i in new_items]
    tool_record = any(k == "tool_call_output_item" for k in kinds)
    handoff_applied: bool | None = None
    if config.handoff:
        handoff_applied = result.last_agent.name == "DiscoveryAgent"

    anchor = obs.first_reply_delta_at or obs.t_tool_started or t0
    return RunRecord(
        runtime="OpenAI Agents SDK (Python)",
        runtime_version=SDK_VERSION,
        config=config.name,
        run_index=schedule.run_index,
        seed=schedule.seed,
        barge_in_offset_s=schedule.barge_in_offset_s,
        tool_delay_s=schedule.tool_delay_s,
        phase_final=obs.phase,
        tool_emitted=obs.tool_started or "tool_call_item" in kinds,
        tool_started=obs.tool_started,
        tool_finished=obs.tool_finished,
        tool_cancelled=obs.tool_cancelled,
        speech_interrupted=not obs.reply_completed,
        tool_record_in_context=tool_record,
        tools_executed_event=None,
        handoff_applied=handoff_applied,
        playback_seconds=None,
        t_tool_started_s=(obs.t_tool_started - t0 if obs.t_tool_started else None),
        t_tool_finished_s=(obs.t_tool_finished - t0 if obs.t_tool_finished else None),
        t_interrupt_s=(cancelled_at - anchor if cancelled_at is not None else None),
        wall_time_s=round(time.perf_counter() - t0, 3),
        notes="; ".join(obs.notes)
        + f"; cancel_mode={cancel_mode}; new_items={kinds}; reply_completed={obs.reply_completed}",
    )
