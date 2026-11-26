"""
gorgon_glue.py — binds UtteranceStateMachine, SessionStateMachine,
idle_generator and model_queue into the chunk-emitting pipeline.

Ownership boundaries:
  - UtteranceStateMachine: semantic state only. Never touches seq/t_ns.
  - SessionStateMachine:   owns seq/t_ns and queue accounting.
  - Gorgon (this class):   owns payload selection and encode() invocation.

Invariants enforced here (I1-I9 from state-diagram §4):
  I1  seq strict +1:       Session.commit() called only AFTER encode() success
                           (peek/commit, thread decision (a)) — no gap window
  I2  t_ns strict step:    advanced only by Session.commit()
  I3  flags match state:   built atomically from Utterance.is_speaking()/is_idle()
  I4  crc valid:           encode() is atomic; frame enqueued only on success
  I5  seq=0 on new WS:     Gorgon is constructed per WebSocket
  I6  bs_count matches:    validated in Gorgon.__init__, immutable per session
  I7  no speech drop:      drop policy applied only when chunk_source() == 'idle'
  I8  no half-chunk:       one frame per tick, selected atomically
  I9  t_ns monotone:       t_ns advanced by Session, not by generators

Changes vs the thread draft (this revision):
  1. _emit() uses peek()/commit() (decision (a)): nothing advances until
     encode() succeeded — an encode failure can no longer open the I1 gap,
     and the session is not force-failed (improvement over fail-closed (b)).
  2. on_model_chunk() rejects chunks via Utterance.accepts_model_chunks():
     stale chunks in IDLE are discarded, and chunks arriving in HANGOVER are
     rejected too (the hold-last tail is authoritative — a late push would
     'resurrect' data after model_done).
  3. ModelQueue protocol requires push()+pop() explicitly; the
     hasattr()/fallback hack and _push_or_fail() are gone.
  4. metrics() reads seq via Session.next_seq_num() — no private access.
  5. The tail buffer is cleared when the utterance returns to IDLE, so a
     stale tail cannot bleed into the next utterance.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Protocol

from facecontract_v141 import (
    encode, FLAG_SPEAKING, FLAG_IDLE, SUPPORTED_BS_COUNTS,
)
from gorgon_state import (
    GorgonConfig, UtteranceState, UtteranceStateMachine,
    SessionStateMachine,
)

log = logging.getLogger("gorgon")


# ── Interfaces for generators (keeps Gorgon testable without real ones) ──────

class IdleGenerator(Protocol):
    def next(self) -> List[float]:
        """Return payload of length bs_count * chunk_size."""


class ModelQueue(Protocol):
    def push(self, payload: List[float]) -> None:
        """Enqueue one model chunk (bs_count * chunk_size floats)."""

    def pop(self) -> Optional[List[float]]:
        """Return next chunk payload, or None if empty."""


# ── Gorgon ────────────────────────────────────────────────────────────────

class Gorgon:
    """
    One instance per WebSocket session. Not thread-safe — single-threaded tick.
    """

    def __init__(
        self,
        config: GorgonConfig,
        bs_count: int,
        idle_gen: IdleGenerator,
        model_queue: ModelQueue,
    ):
        if bs_count not in SUPPORTED_BS_COUNTS:
            raise ValueError(f"bs_count {bs_count} not supported (I6)")

        self.cfg = config
        self.bs_count = bs_count
        self.idle_gen = idle_gen
        self.model_queue = model_queue

        self.utterance = UtteranceStateMachine(config)
        self.session = SessionStateMachine(config)

        # tail buffer: last model payload held during HANGOVER
        self._last_model_payload: Optional[List[float]] = None

        # outbound — transport pulls from here
        self._send_queue: List[bytes] = []

        # metrics
        self.chunks_emitted = 0
        self.chunks_dropped = 0
        self.chunks_encode_failed = 0
        self._tick_count = 0

    # ── lifecycle ─────────────────────────────────────────────────────────

    def on_ws_open(self, start_t_ns: int) -> None:
        self.session.open(start_t_ns)
        log.info("gorgon: ws opened, t_ns=%d, bs_count=%d",
                 start_t_ns, self.bs_count)

    def on_client_stop(self) -> None:
        self.session.on_client_stop()
        log.info("gorgon: draining (queue_depth=%d)", self.session.queue_depth())

    def on_network_fail(self) -> None:
        self.session.on_network_fail()
        log.warning("gorgon: network failure (queue_depth=%d)",
                    self.session.queue_depth())

    # ── event forwarding (VAD / model) ────────────────────────────────

    def on_vad_start(self, now_us: int) -> None:
        self.utterance.on_vad_start(now_us)

    def on_vad_end(self, now_us: int) -> None:
        self.utterance.on_vad_end(now_us)

    def on_model_chunk(self, now_us: int, payload: List[float]) -> None:
        # Single admission gate (minor #3, extended): stale chunks in IDLE are
        # discarded, and chunks arriving in HANGOVER are rejected — the
        # hold-last tail is authoritative after model_done. Queueing either
        # would resurface wrong data in this or the NEXT utterance.
        if not self.utterance.accepts_model_chunks():
            log.warning("model chunk rejected in %s (stale or hangover tail "
                        "authoritative), discarding",
                        self.utterance.state.value)
            return
        if len(payload) != self.bs_count * self.cfg.chunk_size:
            raise ValueError(
                f"model chunk length {len(payload)} != "
                f"{self.bs_count} * {self.cfg.chunk_size}")
        # First chunk from model signals readiness
        if not self._model_has_started():
            self.utterance.on_model_ready(now_us)
        self.model_queue.push(payload)

    def on_model_done(self, now_us: int) -> None:
        self.utterance.on_model_done(now_us)

    def _model_has_started(self) -> bool:
        # Session-agnostic: has the utterance SM already transitioned?
        return self.utterance.state in (
            UtteranceState.SPEECH_STREAMING,
            UtteranceState.SPEECH_HANGOVER,
        )

    # ── tick (called at fps) ──────────────────────────────────────────────

    def tick(self, now_us: int) -> None:
        self._tick_count += 1
        self.utterance.tick(now_us)
        # The tail must not outlive its utterance (stale-tail note, fix #5).
        if (self.utterance.state == UtteranceState.IDLE_STREAMING
                and self._last_model_payload is not None):
            self._last_model_payload = None
        self.session.tick_drain()

        if not self.session.can_emit():
            return

        payload = self._select_payload()
        if payload is None:
            return  # dropped (backpressure) — see _select_payload

        self._emit(payload)

    def _select_payload(self) -> Optional[List[float]]:
        """
        Decide what to play this tick.

        Priority:
          1. model queue, if chunk_source() == 'model'
          2. last model payload during HANGOVER (tail), if queue empty
          3. idle generator otherwise
          4. drop only if BACKPRESSURE and source is 'idle' (I7)

        Returns None only if backpressure dropped the chunk.
        """
        source = self.utterance.chunk_source()

        if source == "model":
            p = self.model_queue.pop()
            if p is not None:
                if len(p) != self.bs_count * self.cfg.chunk_size:
                    raise ValueError(f"model chunk length mismatch: {len(p)}")
                self._last_model_payload = p
                return p
            # queue empty but we're in HANGOVER → hold-last tail (v0 policy)
            if self.utterance.state == UtteranceState.SPEECH_HANGOVER:
                if self._last_model_payload is not None:
                    return self._last_model_payload
            # queue empty in STREAMING — shouldn't happen if model_adapter
            # respects contract (model_ready ⟺ push before done); log + idle
            log.warning("model queue empty in %s, falling back to idle",
                        self.utterance.state.value)
            # fall through to idle

        # source == 'idle' OR model queue empty
        if self.session.should_drop_idle():
            self.chunks_dropped += 1
            return None
        return self.idle_gen.next()

    def _emit(self, payload: List[float]) -> None:
        """peek → encode → commit (decision (a)): nothing advances until
        encode() succeeded, so an encode failure cannot open the I1 gap window
        and does not require failing the session (improvement over (b))."""
        seq, t_ns = self.session.peek()

        flags = 0
        if self.utterance.is_speaking():
            flags |= FLAG_SPEAKING
        if self.utterance.is_idle():
            flags |= FLAG_IDLE

        try:
            frame = encode(
                seq=seq, t_ns=t_ns, dur_us=self.cfg.dur_us,
                fps=self.cfg.fps, chunk_size=self.cfg.chunk_size,
                payload=payload, flags=flags,
                # head_pose: v0 = None; future: pull from head-tracking source
            )
        except Exception:
            # Payload-level bug: do NOT commit, do NOT fail the session —
            # the next tick re-uses the same seq (no gap). I1/I2 untouched.
            self.chunks_encode_failed += 1
            log.exception("encode failed for seq=%d — chunk dropped, "
                          "seq/t_ns NOT advanced (peek/commit)", seq)
            return

        self.session.commit()           # the ONLY advance point (I1/I2)
        self._send_queue.append(frame)
        self.session.on_chunk_enqueued()
        self.chunks_emitted += 1

    # ── transport-facing ──────────────────────────────────────────────

    def take_frame(self) -> Optional[bytes]:
        """Pop one encoded frame for the transport. Returns None if empty."""
        if not self._send_queue:
            return None
        frame = self._send_queue.pop(0)
        self.session.on_chunk_sent()
        return frame

    # ── observability ─────────────────────────────────────────────────────

    def metrics(self) -> dict:
        return {
            "state_utterance": self.utterance.state.value,
            "state_session": self.session.state.value,
            "seq_next": self.session.next_seq_num(),
            "t_ns_next": self.session.current_t_ns(),
            "queue_depth": self.session.queue_depth(),
            "send_queue": len(self._send_queue),
            "chunks_emitted": self.chunks_emitted,
            "chunks_dropped": self.chunks_dropped,
            "chunks_encode_failed": self.chunks_encode_failed,
            "ticks": self._tick_count,
        }
