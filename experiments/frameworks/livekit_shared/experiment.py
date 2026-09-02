"""Drive a real LiveKit Agents `AgentSession` through the phantom-transition scenario.

Runs unchanged under livekit-agents 1.3.10 and 1.7.1 (each in its own venv).
Nothing here talks to a network: the LLM, TTS, VAD and audio sink are
in-process doubles written against the framework's own base classes, in the
shape of the doubles the framework's test-suite uses (tests/fake_*.py in
livekit/agents, Apache-2.0). The session, the speech handle, the tool
executor, the interruption logic and the chat-context bookkeeping are the
framework's own code.

How one run goes:

1. `session.start(agent)` with `session.input.audio` fed by a silent frame
   source and `session.output.audio` a sink that plays frames at wall-clock
   speed and reports playback events, like a room would.
2. `session.generate_reply(user_input="hello there")` opens the agent turn.
   The scripted LLM emits `advance_phase(target="DISCOVERY")` at the
   configured offset and the reply text; the scripted TTS turns each sentence
   into silent PCM at a fixed seconds-per-word rate.
3. The instant the sink reports `playback_started`, a timer is armed for the
   seeded barge-in offset. When it fires, the triggered VAD emits
   START_OF_SPEECH, INFERENCE_DONE ticks and END_OF_SPEECH into the
   framework's audio-recognition pipeline. The framework's own
   `_interrupt_by_audio_activity` decides whether to interrupt the speech.
4. The run waits for the speech handle to resolve plus a settle window, then
   records the phase, the tool's timeline and what the chat context kept.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from livekit import rtc
from livekit.agents import Agent, AgentSession, RunContext, function_tool, llm, tts, utils, vad
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.voice.io import AudioInput, AudioOutput, AudioOutputCapabilities

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

try:
    from livekit.agents import __version__ as LK_VERSION
except ImportError:  # pragma: no cover
    from livekit.agents.version import __version__ as LK_VERSION

SAMPLE_RATE = 24000
SECONDS_PER_WORD = 0.12  # 70 words of reply -> about 8.4 s of audio
VAD_TICK = 0.05


# --------------------------------------------------------------------------
# Scripted LLM: one tool call at a fixed offset, the reply text streamed fast
# --------------------------------------------------------------------------


class ScriptedLLM(llm.LLM):
    def __init__(self, *, tool_emit_offset_s: float) -> None:
        super().__init__()
        self.tool_emit_offset_s = tool_emit_offset_s
        self.calls = 0

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[Any] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **_: Any,
    ) -> llm.LLMStream:
        self.calls += 1
        return ScriptedLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            emit_tool=self.calls == 1,
        )


class ScriptedLLMStream(llm.LLMStream):
    def __init__(
        self,
        llm_: ScriptedLLM,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[Any],
        conn_options: APIConnectOptions,
        emit_tool: bool,
    ) -> None:
        super().__init__(llm_, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._script = llm_
        self._emit_tool = emit_tool

    def _send(self, *, delta: str | None = None, tool_call: bool = False) -> None:
        tool_calls = []
        if tool_call:
            tool_calls = [
                llm.FunctionToolCall(
                    name=TOOL_NAME,
                    arguments=json.dumps({"target": TARGET_PHASE}),
                    call_id="call_advance_1",
                )
            ]
        self._event_ch.send_nowait(
            llm.ChatChunk(
                id="scripted",
                delta=llm.ChoiceDelta(role="assistant", content=delta, tool_calls=tool_calls),
            )
        )

    async def _run(self) -> None:
        if not self._emit_tool:
            # Any later inference (a reply after a tool result or after the
            # barge-in turn) answers with a short sentence and no tool.
            self._send(delta="Understood.")
            return
        t0 = time.perf_counter()
        offset = self._script.tool_emit_offset_s
        if offset <= 0.0:
            self._send(tool_call=True)
        step = 24
        for i in range(0, len(REPLY_TEXT), step):
            self._send(delta=REPLY_TEXT[i : i + step])
            await asyncio.sleep(0.005)
        if offset > 0.0:
            remaining = offset - (time.perf_counter() - t0)
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._send(tool_call=True)


# --------------------------------------------------------------------------
# Scripted TTS: non-streaming, so each sentence becomes audio as it completes
# --------------------------------------------------------------------------


class ScriptedTTS(tts.TTS):
    def __init__(self) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=1,
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return ScriptedChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class ScriptedChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid("scripted_tts_"),
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/pcm",
        )
        words = max(1, len(self._input_text.split()))
        total = int(SAMPLE_RATE * words * SECONDS_PER_WORD)
        pushed = 0
        while pushed < total:
            n = min(SAMPLE_RATE // 100, total - pushed)
            output_emitter.push(b"\x00\x00" * n)
            pushed += n
            await asyncio.sleep(0)
        output_emitter.flush()


# --------------------------------------------------------------------------
# Triggered VAD: silent until fire(), then a speech burst of a given length
# --------------------------------------------------------------------------


class TriggeredVAD(vad.VAD):
    def __init__(self, *, speech_seconds: float) -> None:
        super().__init__(capabilities=vad.VADCapabilities(update_interval=VAD_TICK))
        self.speech_seconds = speech_seconds
        self._fire = asyncio.Event()
        self.fired_at: float | None = None

    def fire(self) -> None:
        self.fired_at = time.perf_counter()
        self._fire.set()

    def stream(self) -> vad.VADStream:
        return TriggeredVADStream(self)


class TriggeredVADStream(vad.VADStream):
    def __init__(self, v: TriggeredVAD) -> None:
        super().__init__(v)
        self._v = v

    async def _main_task(self) -> None:
        # Consume frames so the input channel never backs up, and wait for the trigger.
        drain = asyncio.create_task(self._drain())
        try:
            await self._v._fire.wait()
            t0 = time.perf_counter()
            self._emit(vad.VADEventType.START_OF_SPEECH, 0.0)
            while (elapsed := time.perf_counter() - t0) < self._v.speech_seconds:
                await asyncio.sleep(VAD_TICK)
                self._emit(vad.VADEventType.INFERENCE_DONE, elapsed)
            self._emit(vad.VADEventType.END_OF_SPEECH, self._v.speech_seconds, silence=0.6)
            await drain
        finally:
            drain.cancel()

    async def _drain(self) -> None:
        async for _ in self._input_ch:
            pass

    def _emit(self, kind: vad.VADEventType, speech: float, silence: float = 0.0) -> None:
        self._event_ch.send_nowait(
            vad.VADEvent(
                type=kind,
                samples_index=0,
                timestamp=time.perf_counter(),
                speech_duration=speech,
                silence_duration=silence,
                raw_accumulated_speech=speech,
                raw_accumulated_silence=silence,
            )
        )


# --------------------------------------------------------------------------
# Audio IO: a silent microphone and a sink that plays at wall-clock speed
# --------------------------------------------------------------------------


class SilentMicrophone(AudioInput):
    def __init__(self) -> None:
        super().__init__(label="SilentMicrophone")
        self._ch = utils.aio.Chan[rtc.AudioFrame]()
        self._task: asyncio.Task[None] | None = None

    async def __anext__(self) -> rtc.AudioFrame:
        return await self._ch.__anext__()

    def start(self) -> None:
        self._task = asyncio.create_task(self._feed())

    async def _feed(self) -> None:
        n = 160  # 10 ms at 16 kHz
        frame = rtc.AudioFrame(
            data=b"\x00\x00" * n, sample_rate=16000, num_channels=1, samples_per_channel=n
        )
        try:
            while True:
                self._ch.send_nowait(frame)
                await asyncio.sleep(0.01)
        except (asyncio.CancelledError, utils.aio.ChanClosed):
            pass

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._ch.close()


class PlayoutSink(AudioOutput):
    """Plays captured frames at real-time speed and reports playback events.

    Same design as the FakeAudioOutput in livekit/agents tests/fake_io.py: a
    virtual playout clock started on the first frame, a flush that completes
    after the remaining audio would have played, and a clear_buffer that
    reports how far playout got.
    """

    def __init__(self) -> None:
        super().__init__(
            label="PlayoutSink",
            capabilities=AudioOutputCapabilities(pause=False),
            sample_rate=None,
        )
        self._pushed = 0.0
        self._started_at: float | None = None
        self._flush_handle: asyncio.TimerHandle | None = None
        self.first_frame_at: float | None = None
        self.played_total = 0.0
        self.started = asyncio.Event()

    def _played(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.perf_counter() - self._started_at

    def _reset(self) -> None:
        self._pushed = 0.0
        self._started_at = None
        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        self._pushed += frame.duration
        if self._started_at is None:
            self._started_at = time.perf_counter()
            if self.first_frame_at is None:
                self.first_frame_at = self._started_at
                self.started.set()
            if hasattr(self, "on_playback_started"):  # absent before 1.4
                self.on_playback_started(created_at=time.time())

    def flush(self) -> None:
        super().flush()
        if not self._pushed:
            return
        pushed = self._pushed
        delay = max(pushed - self._played(), 0.0)

        def _done() -> None:
            self.played_total += pushed
            self._reset()
            self.on_playback_finished(playback_position=pushed, interrupted=False)

        if self._flush_handle:
            self._flush_handle.cancel()
        self._flush_handle = asyncio.get_event_loop().call_later(delay, _done)

    def clear_buffer(self) -> None:
        if not self._pushed:
            return
        position = min(self._played(), self._pushed)
        self.played_total += position
        self._reset()
        self.on_playback_finished(playback_position=position, interrupted=True)


# --------------------------------------------------------------------------
# The agent under test
# --------------------------------------------------------------------------


@dataclass
class Observed:
    phase: str = INITIAL_PHASE
    tool_started: bool = False
    tool_finished: bool = False
    tool_cancelled: bool = False
    t_tool_started: float | None = None
    t_tool_finished: float | None = None
    tools_executed_events: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_agent(config: Config, schedule: Schedule, obs: Observed, t0: float) -> Agent:
    class PhaseAgent(Agent):
        def __init__(self) -> None:
            super().__init__(
                instructions="You are a phase-gated assistant. Advance phases with the tool."
            )

        @function_tool(name=TOOL_NAME)
        async def advance_phase(self, ctx: RunContext, target: str) -> str:
            """Advance the call to the next phase.

            Args:
                target: The phase to advance to.
            """
            obs.tool_started = True
            obs.t_tool_started = time.perf_counter() - t0
            if config.disallow_interruptions:
                try:
                    ctx.disallow_interruptions()
                except RuntimeError as e:
                    obs.notes.append(f"disallow_interruptions raised: {e}")
            try:
                if schedule.tool_delay_s > 0:
                    await asyncio.sleep(schedule.tool_delay_s)
                obs.phase = target
                obs.tool_finished = True
                obs.t_tool_finished = time.perf_counter() - t0
                if config.handoff:
                    # The production shape: mutate, then hand off to the next
                    # phase's agent by returning it.
                    return NextPhaseAgent()
                return f"phase is now {target}"
            except asyncio.CancelledError:
                obs.tool_cancelled = True
                raise

    class NextPhaseAgent(Agent):
        def __init__(self) -> None:
            super().__init__(instructions="You are the discovery-phase assistant.")

    return PhaseAgent()


def make_session(v: TriggeredVAD, tts_: ScriptedTTS, llm_: ScriptedLLM) -> AgentSession:
    """Build the session with the same knobs on both versions.

    Interruption by VAD after 0.5 s of speech (the framework default and the
    upstream test default), no false-interruption pause (the sink cannot
    pause, so the interrupt path is taken, matching a barge-in that cuts the
    agent off), no preemptive generation (production sets it False).
    """
    major_minor = tuple(int(x) for x in LK_VERSION.split(".")[:2])
    if major_minor >= (1, 4):
        from livekit.agents import TurnHandlingOptions
        from livekit.agents.voice.turn import EndpointingOptions, InterruptionOptions

        return AgentSession(
            vad=v,
            stt=None,
            llm=llm_,
            tts=tts_,
            turn_handling=TurnHandlingOptions(
                turn_detection="vad",
                endpointing=EndpointingOptions(min_delay=0.5, max_delay=3.0),
                interruption=InterruptionOptions(
                    mode="vad",
                    min_duration=0.5,
                    min_words=0,
                    resume_false_interruption=False,
                    false_interruption_timeout=None,
                ),
                preemptive_generation={"enabled": False},
            ),
            aec_warmup_duration=None,
        )
    return AgentSession(
        vad=v,
        stt=None,
        llm=llm_,
        tts=tts_,
        turn_detection="vad",
        min_interruption_duration=0.5,
        min_interruption_words=0,
        min_endpointing_delay=0.5,
        max_endpointing_delay=3.0,
        resume_false_interruption=False,
        false_interruption_timeout=None,
        preemptive_generation=False,
    )


async def run_one(config: Config, schedule: Schedule, *, settle_s: float = 1.5) -> RunRecord:
    t0 = time.perf_counter()
    obs = Observed()
    v = TriggeredVAD(speech_seconds=1.2)
    tts_ = ScriptedTTS()
    llm_ = ScriptedLLM(tool_emit_offset_s=config.tool_emit_offset_s)
    session = make_session(v, tts_, llm_)
    mic = SilentMicrophone()
    sink = PlayoutSink()
    session.input.audio = mic
    session.output.audio = sink
    session.on("function_tools_executed", obs.tools_executed_events.append)

    agent = build_agent(config, schedule, obs, t0)
    await session.start(agent)
    mic.start()
    await asyncio.sleep(0.2)  # let the recognition pipeline attach to the mic

    handle = session.generate_reply(user_input=USER_UTTERANCE)
    interrupt_seen_at: float | None = None
    try:
        await asyncio.wait_for(sink.started.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        obs.notes.append("agent never started speaking")
    else:
        loop = asyncio.get_running_loop()
        loop.call_later(schedule.barge_in_offset_s, v.fire)

    # Wait for the speech handle to resolve, then a settle window so the
    # interrupted path's bookkeeping (and any in-flight tool) completes.
    deadline = time.perf_counter() + 30.0
    while not handle.done() and time.perf_counter() < deadline:
        await asyncio.sleep(0.02)
        if interrupt_seen_at is None and handle.interrupted:
            interrupt_seen_at = time.perf_counter() - t0
    if interrupt_seen_at is None and handle.interrupted:
        interrupt_seen_at = time.perf_counter() - t0
    await asyncio.sleep(settle_s)
    if schedule.tool_delay_s > 0 and not obs.tool_finished and not obs.tool_cancelled:
        # give an in-flight tool that nobody cancelled time to reach its mutation
        await asyncio.sleep(schedule.tool_delay_s)

    handoff_applied: bool | None = None
    if config.handoff:
        handoff_applied = session.current_agent is not agent

    items = list(agent.chat_ctx.items)
    calls = [i for i in items if i.type == "function_call" and i.name == TOOL_NAME]
    outs = [i for i in items if i.type == "function_call_output"]
    tool_record_in_context = bool(calls and outs)
    assistant_msgs = [i for i in items if i.type == "message" and i.role == "assistant"]
    interrupted_flags = [getattr(m, "interrupted", None) for m in assistant_msgs]

    await mic.stop()
    try:
        await asyncio.wait_for(session.aclose(), timeout=15.0)
    except Exception as e:  # noqa: BLE001
        obs.notes.append(f"aclose: {type(e).__name__}: {e}")

    notes = list(obs.notes)
    notes.append(f"assistant messages interrupted flags={interrupted_flags}")
    notes.append(f"function_tools_executed events={len(obs.tools_executed_events)}")
    if obs.tools_executed_events:
        ev = obs.tools_executed_events[0]
        outs_ev = getattr(ev, "function_call_outputs", None) or []
        notes.append(
            "event outputs="
            + json.dumps(
                [
                    None if o is None else {"output": getattr(o, "output", None), "is_error": getattr(o, "is_error", None)}
                    for o in outs_ev
                ]
            )
        )

    return RunRecord(
        runtime="LiveKit Agents (Python)",
        runtime_version=LK_VERSION,
        config=config.name,
        run_index=schedule.run_index,
        seed=schedule.seed,
        barge_in_offset_s=schedule.barge_in_offset_s,
        tool_delay_s=schedule.tool_delay_s,
        phase_final=obs.phase,
        tool_emitted=llm_.calls >= 1 and (obs.tool_started or bool(calls)),
        tool_started=obs.tool_started,
        tool_finished=obs.tool_finished,
        tool_cancelled=obs.tool_cancelled,
        speech_interrupted=bool(handle.interrupted),
        tool_record_in_context=tool_record_in_context,
        tools_executed_event=bool(obs.tools_executed_events),
        handoff_applied=handoff_applied,
        playback_seconds=round(sink.played_total, 3),
        t_tool_started_s=(round(obs.t_tool_started - (sink.first_frame_at - t0), 3) if obs.t_tool_started is not None and sink.first_frame_at else obs.t_tool_started),
        t_tool_finished_s=(round(obs.t_tool_finished - (sink.first_frame_at - t0), 3) if obs.t_tool_finished is not None and sink.first_frame_at else obs.t_tool_finished),
        t_interrupt_s=(round(interrupt_seen_at - (sink.first_frame_at - t0), 3) if interrupt_seen_at is not None and sink.first_frame_at else interrupt_seen_at),
        wall_time_s=round(time.perf_counter() - t0, 3),
        notes="; ".join(notes),
    )
