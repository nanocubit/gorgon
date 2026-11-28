"""livekit_bridge.py — LiveKit Agents ⇄ Gorgon bridge.

Wires a LiveKit voice agent (VAD → STT → LLM → TTS) into the Gorgon
chunk-emitting pipeline (gorgon_glue.Gorgon) and publishes FaceContract
v1.4.1 binary frames over the LiveKit Data Channel.

Data Channel topics (reliable=True):
  avatar.fc14.v1     binary — one FaceContract frame per message, exactly
                     what GorgonMetaHuman (UE5) / web clients consume
  avatar.triggers.v1 JSON  — {"type": "trigger_reaction", "reaction": ...}
                     (same shape as gorgon_ws_bridge.py text messages)
  avatar.state.v1    JSON  — {"type": "state", "session_id", "utterance",
                     "speaking", "seq_next", "t_ns_next", "queue_depth"}

Dependencies (NOT imported at module load — the wire-side classes below run
without livekit installed, which is what the test suite and gorgon_ws_bridge
rely on):
    pip install "livekit-agents[silero,openai,deepgram,cartesia]~=0.12"

LiveKit API generation: this file targets the VoiceAssistant (v0.x pipeline
generation) API. The integration surface that MUST stay stable is narrow and
isolated in build_voice_assistant() and the GorgonLiveKitBridge event
handlers; if a newer agents SDK changes event names, only those places move.

Audio → blendshape: AudioToBlendshapeConverter is a DELIBERATE placeholder
(RMS energy → jawOpen), replaced later by an ML adapter (audio2face-class).
It is wired through the same ModelQueue contract as a real face model, so
swapping it does not touch the pipeline, the state machines or the wire
format.

Chunk-period alignment note (fix vs the chat draft): the draft buffered
`chunk_size * 512` bytes labelled "~17ms @ 24kHz" — that arithmetic was
wrong (2048 bytes @ 24kHz/16-bit mono is ~42.7ms, and neither is a chunk
period). The converter now buffers exactly one chunk period:
  samples_per_chunk = (24000 // fps) * chunk_size   # 3200 @ fps=30, cs=4
  target_bytes      = samples_per_chunk * 2          # 6400 bytes = 133.33ms
which is what on_model_chunk() expects to receive per tick.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from facecontract_v141 import NAME_TO_INDEX
from gorgon_glue import Gorgon
from gorgon_state import GorgonConfig

log = logging.getLogger("gorgon.livekit")

TOPIC_FRAMES = "avatar.fc14.v1"
TOPIC_TRIGGERS = "avatar.triggers.v1"
TOPIC_STATE = "avatar.state.v1"

DEFAULT_SAMPLE_RATE = 24_000   # LiveKit agents TTS output (16-bit LE mono)


# ── wire-side model queue (deque adapter for the ModelQueue protocol) ──────────────

class DequeModelQueue:
    """collections.deque fitted to gorgon_glue.ModelQueue (push/pop)."""

    def __init__(self, maxlen: int = 60):
        self._q: deque = deque(maxlen=maxlen)

    def push(self, payload: List[float]) -> None:
        self._q.append(payload)

    def pop(self) -> Optional[List[float]]:
        return self._q.popleft() if self._q else None

    def __len__(self) -> int:
        return len(self._q)


class RecordingModelQueue(DequeModelQueue):
    """ModelQueue that remembers the last chunk Gorgon actually CONSUMED.

    Load-bearing for the speech→idle crossfade: gorgon_glue.tick() clears
    Gorgon._last_model_payload the moment the utterance returns to IDLE
    (stale-tail fix #5) — on the boundary tick itself, BEFORE the idle
    generator is asked for its first chunk. The bridge therefore cannot
    source the fade tail from Gorgon; it must record the last pop() here.
    """

    def __init__(self, maxlen: int = 60):
        super().__init__(maxlen=maxlen)
        self.last_popped: Optional[List[float]] = None

    def pop(self) -> Optional[List[float]]:
        payload = super().pop()
        if payload is not None:
            self.last_popped = payload
        return payload


# ── idle generator with server-side speech→idle crossfade ──────────────────────

@dataclass(frozen=True)
class IdleGeneratorConfig:
    fps: int = 30
    bs_count: int = 32
    chunk_size: int = 4         # must match GorgonConfig.chunk_size
    fade_subframes: int = 6      # 6 sub-frames @ 30fps ≈ 200 ms (§7.2 spirit)


class BreathingIdleGenerator:
    """Idle payload source: breathing on jawOpen + blink envelope.

    Implements gorgon_glue.IdleGenerator (next()). The speech→idle crossfade
    arms LAZILY: on the first next() after an utterance, the generator pulls
    the hold-last tail through the tail_provider (Gorgon._last_model_payload)
    and ramps its fade_subframes from that row to the breathing row. Arming
    lazily is load-bearing: the first idle chunk is generated INSIDE
    gorgon.tick() on the SPEAKING→IDLE boundary tick — any earlier arming
    (e.g. at on_agent_speech_committed) sees an empty queue and a None tail.
    """

    def __init__(self, config: IdleGeneratorConfig):
        self.cfg = config
        self._t = 0                                          # sub-frame clock
        self._fade_from: Optional[List[float]] = None        # speech tail row
        self._fade_left = 0
        self._tail_provider = None                 # Callable[[], Optional]
        self._faded_this_utterance = False

    def set_tail_provider(self, provider) -> None:
        """Provider returning the current hold-last tail (or None)."""
        self._tail_provider = provider

    def mark_utterance_start(self) -> None:
        """New utterance: re-enable the one-shot lazy crossfade and cancel any
        in-progress fade — a stale leftover blend must never bleed into the
        next utterance's post-speech idle."""
        self._faded_this_utterance = False
        self._fade_from = None
        self._fade_left = 0

    def next(self) -> List[float]:
        """One chunk: chunk_size rows × bs_count floats (row-major)."""
        if (not self._faded_this_utterance and self._tail_provider is not None
                and self._fade_left == 0):
            tail = self._tail_provider()
            if tail:
                self.on_speech_end(tail)
        cs = self.cfg.chunk_size
        rows: List[List[float]] = [self._row(i) for i in range(cs)]
        self._t += cs
        return [v for row in rows for v in row]

    def _row(self, i: int) -> List[float]:
        n = self.cfg.bs_count
        row = [0.001] * n
        if n < 32:
            # test-scale levels: keep jawOpen at index 0 semantics minimal
            row[0] = self._breathing(i)
            return self._maybe_fade(row, i)
        idx = NAME_TO_INDEX
        row[idx["eyeBlinkLeft"]] = self._blink(i)
        row[idx["eyeBlinkRight"]] = self._blink(i)
        row[idx["jawOpen"]] = self._breathing(i)
        row[idx["mouthClose"]] = 0.010 + 0.004 * math.sin(
            2 * math.pi * (self._t + i) / 32)
        row[idx["mouthSmileLeft"]] = 0.06
        row[idx["mouthSmileRight"]] = 0.06
        return self._maybe_fade(row, i)

    def _breathing(self, i: int) -> float:
        return 0.032 + 0.026 * math.sin(2 * math.pi * (self._t + i) / 32
                                        - math.pi / 2)

    @staticmethod
    def _blink(i: int) -> float:
        env = {5: 0.4, 6: 0.9, 7: 1.0, 8: 1.0, 9: 0.5, 10: 0.15, 11: 0.05}
        return env.get(i, 0.001)

    def _maybe_fade(self, row: List[float], i: int) -> List[float]:
        if self._fade_left > 0 and self._fade_from is not None \
                and len(self._fade_from) == len(row):
            w = self._fade_left / self.cfg.fade_subframes
            row = [self._fade_from[k] * w + row[k] * (1.0 - w)
                   for k in range(len(row))]
            self._fade_left -= 1
        return row

    def on_speech_end(self, last_payload: Optional[List[float]]) -> None:
        """Arm the crossfade from the last SPEAKING chunk (hold-last tail).

        last_payload is the model tail in Gorgon row-major layout
        [frame][bs]; the fade source is its LAST sub-frame row.
        """
        if not last_payload:
            self._fade_from = None
            self._fade_left = 0
            return
        cs = self.cfg.chunk_size
        bs = self.cfg.bs_count
        n = len(last_payload)
        if n == bs * cs and n >= bs:
            self._fade_from = last_payload[n - bs:n]
            self._fade_left = self.cfg.fade_subframes
            self._faded_this_utterance = True   # one-shot per utterance
        else:
            self._fade_from = None
            self._fade_left = 0


# ── audio → blendshape placeholder (RMS energy → jawOpen) ──────────────────────

class AudioToBlendshapeConverter:
    """PCM 24kHz / 16-bit / mono → Gorgon model chunks.

    PLACEHOLDER by design (RMS → jawOpen, fixed smile), swapped later for an
    ML adapter. Contract with the pipeline: feed_audio() accumulates PCM and
    emits exactly one chunk (bs_count * chunk_size floats, row-major) per
    chunk period; feed() returns the chunk or None while buffering.
    """

    def __init__(self, bs_count: int = 32, fps: int = 30, chunk_size: int = 4,
                 sample_rate: int = DEFAULT_SAMPLE_RATE,
                 rms_divisor: float = 10_000.0):
        self.bs_count = bs_count
        self.fps = fps
        self.chunk_size = chunk_size
        self.sample_rate = sample_rate
        self.rms_divisor = rms_divisor
        samples_per_chunk = (sample_rate // fps) * chunk_size
        self._target_bytes = samples_per_chunk * 2   # 16-bit → 2 bytes/sample
        self._buf = bytearray()
        self._jaw_idx = NAME_TO_INDEX["jawOpen"] if bs_count >= 32 else 0
        self._smile_l = NAME_TO_INDEX.get("mouthSmileLeft") if bs_count >= 32 \
            else None
        self._smile_r = NAME_TO_INDEX.get("mouthSmileRight") if bs_count >= 32 \
            else None

    def feed_audio(self, pcm_bytes: bytes) -> Optional[List[float]]:
        """Push PCM bytes; return a finished chunk payload or None."""
        self._buf.extend(pcm_bytes)
        if len(self._buf) < self._target_bytes:
            return None
        window = bytes(self._buf[:self._target_bytes])
        del self._buf[:self._target_bytes]
        return self._pcm_to_chunk(window)

    def flush(self) -> Optional[List[float]]:
        """Emit the partial buffer as a final (zero-padded) chunk, if any."""
        if not self._buf:
            return None
        window = bytes(self._buf)
        self._buf.clear()
        return self._pcm_to_chunk(window)

    def _pcm_to_chunk(self, window: bytes) -> List[float]:
        jaw = self._rms_energy(window) * 0.8
        payload = [0.0] * (self.bs_count * self.chunk_size)
        for f in range(self.chunk_size):
            base = f * self.bs_count
            payload[base + self._jaw_idx] = jaw
            if self._smile_l is not None:
                payload[base + self._smile_l] = 0.1
            if self._smile_r is not None:
                payload[base + self._smile_r] = 0.1
        return payload

    def _rms_energy(self, window: bytes) -> float:
        import struct
        n = len(window) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack(f"<{n}h", window)
        sq = 0.0
        for s in samples:
            sq += float(s) * float(s)
        rms = math.sqrt(sq / n)
        return min(1.0, rms / self.rms_divisor)


# ── LiveKit bridge ──────────────────────────────────────────────────────────────

class GorgonLiveKitBridge:
    """One bridge per LiveKit room/participant.

    Wire-side only dependencies are injected; the livekit objects (room,
    assistant) are duck-typed so the class is testable without the SDK.
    """

    def __init__(
        self,
        room: Any,
        bs_count: int = 32,
        fps: int = 30,
        chunk_size: int = 4,
        session_id: str = "",
        user_external_id: str = "",
        data_layer: Optional[Any] = None,
    ):
        self.room = room
        self.session_id = session_id
        self.user_external_id = user_external_id
        self._data_layer = data_layer        # optional publisher override

        self.cfg = GorgonConfig(fps=fps, chunk_size=chunk_size)
        self.model_queue = RecordingModelQueue(maxlen=60)
        self.idle_gen = BreathingIdleGenerator(
            IdleGeneratorConfig(fps=fps, bs_count=bs_count,
                                chunk_size=chunk_size))
        self.gorgon = Gorgon(self.cfg, bs_count=bs_count,
                             idle_gen=self.idle_gen,
                             model_queue=self.model_queue)
        self.converter = AudioToBlendshapeConverter(
            bs_count=bs_count, fps=fps, chunk_size=chunk_size)
        # lazy crossfade: the idle generator pulls the hold-last tail itself
        self.idle_gen.set_tail_provider(self._last_speech_payload)

        self._started_ns = 0
        self._tick_task: Optional[asyncio.Task] = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Open the Gorgon session; start the tick loop only when an asyncio
        loop is already running (embedders/tests may tick synchronously via
        tick_once() instead)."""
        self._started_ns = time.time_ns()
        self.gorgon.on_ws_open(start_t_ns=0)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._tick_task = loop.create_task(self._tick_loop())
        self._publish_state()

    async def close(self) -> None:
        if self._tick_task is not None:
            self._tick_task.cancel()
            self._tick_task = None

    def _now_us(self) -> int:
        return (time.time_ns() - self._started_ns) // 1_000

    # ── tick (one chunk period; called by the loop or synchronously) ──────

    def tick_once(self, now_us: int) -> None:
        """Advance the pipeline one tick. The idle crossfade arms lazily inside
        the idle generator (see BreathingIdleGenerator.next()) — the first
        idle chunk is produced inside gorgon.tick() on the SPEAKING→IDLE
        boundary tick, so arming must happen in the generator, not here."""
        self.gorgon.tick(now_us)

    async def _tick_loop(self) -> None:
        period = self.cfg.dur_ns / 1_000_000.0     # ms per chunk period
        next_deadline = time.monotonic()
        try:
            while True:
                self.tick_once(self._now_us())
                while True:
                    frame = self.gorgon.take_frame()
                    if frame is None:
                        break
                    self._publish_frame(frame)
                next_deadline += period / 1000.0
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_deadline = time.monotonic()   # clock slipped; resync
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tick loop crashed; stopping frame publishing")

    # ── agent event wiring ──────────────────────────────────────────────

    def on_user_started_speaking(self) -> None:
        self.gorgon.on_vad_start(self._now_us())
        self.idle_gen.mark_utterance_start()
        self._publish_state()

    def on_user_stopped_speaking(self) -> None:
        self.gorgon.on_vad_end(self._now_us())
        self._publish_state()

    def on_tts_audio(self, pcm_bytes: bytes) -> None:
        """Feed TTS PCM; every finished chunk goes through on_model_chunk()."""
        now_us = self._now_us()
        chunk = self.converter.feed_audio(pcm_bytes)
        if chunk is not None:
            self.gorgon.on_model_chunk(now_us, chunk)

    def on_agent_speech_committed(self) -> None:
        """End of TTS for the utterance: flush the converter and signal EOS.

        The idle crossfade is NOT armed here on purpose — the model queue may
        still hold unconsumed chunks, so the hold-last tail is not final yet;
        and the first idle chunk is generated inside a later gorgon.tick().
        The generator's lazy tail-provider arming covers both.
        """
        now_us = self._now_us()
        tail_chunk = self.converter.flush()
        if tail_chunk is not None:
            self.gorgon.on_model_chunk(now_us, tail_chunk)
        self.gorgon.on_model_done(now_us)
        # NOTE: the idle crossfade is NOT armed here — the model queue may
        # still hold unconsumed chunks and gorgon_glue clears the tail at the
        # boundary tick. The generator's lazy tail-provider arming (fed from
        # RecordingModelQueue.last_popped) covers both.
        self._publish_state()

    def _last_speech_payload(self) -> Optional[List[float]]:
        """The last RENDERED speech chunk (last model pop) — survives the
        boundary-tick stale-tail clear in gorgon_glue.tick()."""
        return self.model_queue.last_popped

    def on_trigger(self, reaction: str) -> None:
        self._publish_trigger(reaction)

    # ── Data Channel publishing ──────────────────────────────────────────

    def _publish(self, data: bytes, topic: str) -> None:
        if self._data_layer is not None:
            self._data_layer.publish_data(data, topic=topic, reliable=True)
            return
        local = getattr(self.room, "local_participant", None)
        if local is None:
            log.warning("no local_participant; dropping %s message", topic)
            return
        try:
            local.publish_data(data, topic=topic, reliable=True)
        except TypeError:
            # older SDKs without the topic kwarg
            local.publish_data(data, reliable=True)

    def _publish_frame(self, frame: bytes) -> None:
        self._publish(frame, TOPIC_FRAMES)

    def _publish_trigger(self, reaction: str) -> None:
        doc = {"type": "trigger_reaction", "reaction": reaction,
               "session_id": self.session_id}
        self._publish(json.dumps(doc).encode("utf-8"), TOPIC_TRIGGERS)

    def _publish_state(self) -> None:
        m = self.gorgon.metrics()
        doc = {
            "type": "state",
            "session_id": self.session_id,
            "user_external_id": self.user_external_id,
            "utterance": m["state_utterance"],
            "speaking": m["state_utterance"] in ("speech_streaming",
                                                 "speech_hangover"),
            "seq_next": m["seq_next"],
            "t_ns_next": m["t_ns_next"],
            "queue_depth": m["queue_depth"],
            "chunks_emitted": m["chunks_emitted"],
        }
        self._publish(json.dumps(doc).encode("utf-8"), TOPIC_STATE)


# ── LiveKit Agents assembly (optional, requires the SDK) ──────────────────────

def build_voice_assistant(bridge: "GorgonLiveKitBridge") -> Any:
    """Assemble the v0.x VoiceAssistant with the recommended plugin set.

    TTS: Cartesia ("sonic", ru voice) — the approved TTS decision (TTFT
    ~150ms, native ru streaming). STT: Deepgram ru. LLM: OpenAI.

    The TTS output is teed into bridge.on_tts_audio() through a wrapped
    ChunkedStream so the blendshape converter sees the same PCM the agent
    plays into the room.
    """
    try:
        from livekit import agents                      # noqa: PLC0415
        from livekit.agents.pipeline import VoiceAssistant
        from livekit.plugins import cartesia, deepgram, openai, silero
    except ImportError as exc:                          # pragma: no cover
        raise RuntimeError(
            "livekit-agents is required for build_voice_assistant(); "
            'pip install "livekit-agents[silero,openai,deepgram,cartesia]~=0.12"'
        ) from exc

    base_tts = cartesia.TTS(model="sonic", language="ru")

    class TeeingChunkedStream(agents.tts.ChunkedStream):
        """ChunkedStream wrapper: forward each PCM frame to the bridge."""

        def __init__(self, tts, text, opts, bridge_ref):
            super().__init__(tts=tts, text=text, opts=opts)
            self._bridge_ref = bridge_ref

        async def _main_gen(self):
            async for evt in super()._main_gen():
                if self._bridge_ref is not None and evt is not None:
                    data = getattr(evt, "data", None)
                    if data:
                        self._bridge_ref.on_tts_audio(bytes(data))
                yield evt

    class TeeingTTS(base_tts.__class__):
        def synthesize(self, text: str, *args, **kwargs):
            stream = super().synthesize(text, *args, **kwargs)
            stream.__class__ = TeeingChunkedStream
            stream._bridge_ref = bridge_ref
            return stream

    assistant = VoiceAssistant(
        vad=silero.VAD.load(),
        stt=deepgram.STT(language="ru"),
        llm=openai.LLM(),
        tts=TeeingTTS(),
    )

    @assistant.on("user_started_speaking")
    def _user_started(_agent):                      # pragma: no cover
        bridge.on_user_started_speaking()

    @assistant.on("user_stopped_speaking")
    def _user_stopped(_agent):                      # pragma: no cover
        bridge.on_user_stopped_speaking()

    @assistant.on("agent_speech_committed")
    def _committed(_agent):                         # pragma: no cover
        bridge.on_agent_speech_committed()

    return assistant


async def entrypoint(ctx) -> None:                  # pragma: no cover
    """livekit-agents Worker entrypoint (v0.x JobContext)."""
    bridge = GorgonLiveKitBridge(room=ctx.room,
                                 session_id=ctx.job.id,
                                 user_external_id="")
    bridge.initialize()
    await ctx.connect()
    assistant = build_voice_assistant(bridge)
    assistant.start(ctx.room)
    await asyncio.Event().wait()


def main() -> None:                                 # pragma: no cover
    """Run as `python livekit_bridge.py dev|start` (livekit.cli passthrough)."""
    from livekit.agents import cli
    cli.run_app(entrypoint)


if __name__ == "__main__":                          # pragma: no cover
    main()
