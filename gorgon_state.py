"""
gorgon_state.py — UtteranceStateMachine (semantic) and SessionStateMachine
(seq/t_ns + queue accounting) for the Gorgon pipeline.

Ownership boundaries (see gorgon_glue.py):
  - UtteranceStateMachine: semantic state only. Never touches seq/t_ns.
  - SessionStateMachine:   owns seq/t_ns and queue accounting.

Invariants (state-diagram §4):
  I1  seq strict +1:     seq advances ONLY in Session.commit() (+1 per frame)
  I2  t_ns strict step:  t_ns advances ONLY in Session.commit() (+dur_ns)
  I3  flags match state: is_speaking()/is_idle() are total, disjoint and cover
                         every state
  I5  seq=0 on new WS:   open() resets counters (Gorgon is per-WebSocket)
  I7  no speech drop:    should_drop_idle() is the only drop gate; callers apply
                         it exclusively to idle-sourced chunks
  I9  t_ns monotone:     t_ns never decreases (no setter at all)

Failure-window fix (thread decision (a) — peek/commit):
  next_seq() used to advance _seq/_t_ns BEFORE encode() ran; a failing encode
  left seq=N unsent with _seq=N+1 — a client-visible gap violating I1 (the
  client would start interpolating per PROTOCOL.md §2.3 with nothing to
  interpolate). commit() is now the single advance point and Gorgon calls it
  only AFTER encode() succeeded, so a failed encode leaves (seq, t_ns)
  untouched and the next tick re-uses the same seq: no gap, no forced
  disconnect.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

log = logging.getLogger("gorgon")


@dataclass(frozen=True)
class GorgonConfig:
    fps: int = 30
    chunk_size: int = 4          # frames per chunk (bs_count = model pop size)
    hangover_ms: int = 500
    max_queue_depth: int = 8     # in-flight chunks before idle backpressure

    @property
    def dur_ns(self) -> int:
        """Nominal chunk duration in ns (integer floor; dur_us is advisory)."""
        return self.chunk_size * 1_000_000_000 // self.fps

    @property
    def dur_us(self) -> int:
        return self.dur_ns // 1000


class UtteranceState(Enum):
    IDLE_STREAMING = "idle_streaming"
    BUFFERING = "buffering"
    SPEECH_STREAMING = "speech_streaming"
    SPEECH_HANGOVER = "speech_hangover"


class UtteranceStateMachine:
    """Semantic state only (VAD/model lifecycle). No seq/t_ns here."""

    def __init__(self, config: GorgonConfig):
        self.config = config
        self.state = UtteranceState.IDLE_STREAMING
        self._hangover_start_us: Optional[int] = None
        self._model_done_seen: bool = False

    # ── events ────────────────────────────────────────────────────────

    def on_vad_start(self, now_us: int) -> None:
        if self.state == UtteranceState.IDLE_STREAMING:
            self._enter_buffering()
        elif self.state == UtteranceState.SPEECH_HANGOVER:
            # Mid-word pause: VAD resumed inside the hangover window. Cancel
            # the hangover deadline FIRST (it must not ghost-fire later), then
            # resume into STREAMING if the model is still producing, or into
            # BUFFERING if model_done was already seen (fresh output needed).
            self._hangover_start_us = None
            if self._model_done_seen:
                self._enter_buffering()
            else:
                self.state = UtteranceState.SPEECH_STREAMING
        # BUFFERING/STREAMING: duplicate event → no-op

    def on_vad_end(self, now_us: int) -> None:
        if self.state == UtteranceState.SPEECH_STREAMING:
            self._enter_hangover(now_us)
        elif self.state == UtteranceState.BUFFERING:
            # user stopped before the model produced anything → abort
            self._enter_idle()
        # IDLE/HANGOVER: no-op

    def on_model_ready(self, now_us: int) -> None:
        if self.state == UtteranceState.BUFFERING:
            self.state = UtteranceState.SPEECH_STREAMING
        elif self.state == UtteranceState.IDLE_STREAMING:
            log.warning("on_model_ready in IDLE (stale model output?) — ignored")

    def on_model_done(self, now_us: int) -> None:
        if self.state == UtteranceState.SPEECH_STREAMING:
            self._model_done_seen = True
            self._enter_hangover(now_us)
        elif self.state == UtteranceState.SPEECH_HANGOVER:
            self._model_done_seen = True  # duplicate EOS
        elif self.state == UtteranceState.BUFFERING:
            log.warning("on_model_done in BUFFERING (no chunk ever arrived) — abort")
            self._enter_idle()

    def tick(self, now_us: int) -> None:
        if self.state == UtteranceState.SPEECH_HANGOVER:
            elapsed = now_us - (self._hangover_start_us or 0)
            if elapsed >= self.config.hangover_ms * 1000:
                self._enter_idle()

    # ── predicates (I3: total, disjoint, cover every state) ───────────────

    def is_speaking(self) -> bool:
        return self.state in (UtteranceState.SPEECH_STREAMING,
                              UtteranceState.SPEECH_HANGOVER)

    def is_idle(self) -> bool:
        return not self.is_speaking()

    def chunk_source(self) -> str:
        return "model" if self.is_speaking() else "idle"

    def accepts_model_chunks(self) -> bool:
        """Model chunks are accepted ONLY in BUFFERING or SPEECH_STREAMING.

        IDLE: stale output of a cancelled/finished utterance — queueing it
        would make it resurface in the NEXT utterance.
        HANGOVER: the tail (hold-last) is authoritative; a chunk pushed here
        would become the new tail and 'resurrect' data after model_done
        (adapter protocol violation or out-of-order delivery)."""
        return self.state in (UtteranceState.BUFFERING,
                              UtteranceState.SPEECH_STREAMING)

    # ── internals ─────────────────────────────────────────────────────────

    def _enter_hangover(self, now_us: int) -> None:
        self.state = UtteranceState.SPEECH_HANGOVER
        self._hangover_start_us = now_us

    def _enter_buffering(self) -> None:
        self.state = UtteranceState.BUFFERING
        self._model_done_seen = False
        self._hangover_start_us = None

    def _enter_idle(self) -> None:
        self.state = UtteranceState.IDLE_STREAMING
        self._model_done_seen = False
        self._hangover_start_us = None

    @property
    def hangover_start_us(self) -> Optional[int]:
        return self._hangover_start_us


class SessionState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    DRAINING = "draining"
    FAILED = "failed"


class SessionStateMachine:
    """Owns seq/t_ns (I1, I2, I5, I9) and in-flight queue accounting.

    seq/t_ns advance ONLY in commit() — call it strictly after the frame for
    the peeked (seq, t_ns) was successfully encoded."""

    def __init__(self, config: GorgonConfig):
        self.config = config
        self.state = SessionState.CLOSED
        self._seq = 0
        self._t_ns = 0
        self._in_flight = 0  # enqueued into send path, not yet taken by transport

    # ── lifecycle ─────────────────────────────────────────────────────────

    def open(self, start_t_ns: int) -> None:
        if self.state is not SessionState.CLOSED:
            raise RuntimeError(f"open(): session already {self.state.value}")
        self._seq = 0            # I5: seq=0 on new WS
        self._t_ns = start_t_ns  # I9: monotone base
        self._in_flight = 0
        self.state = SessionState.OPEN

    def on_client_stop(self) -> None:
        if self.state is SessionState.OPEN:
            self.state = SessionState.DRAINING

    def on_network_fail(self) -> None:
        if self.state in (SessionState.OPEN, SessionState.DRAINING):
            self.state = SessionState.FAILED

    def tick_drain(self) -> None:
        """Reserved for drain accounting; DRAINING ends when the transport has
        pulled everything that was already emitted."""

    # ── emit gate ─────────────────────────────────────────────────────────

    def can_emit(self) -> bool:
        return self.state is SessionState.OPEN

    def should_drop_idle(self) -> bool:
        """Backpressure gate. I7: callers apply this ONLY to idle chunks."""
        return (self.state is SessionState.OPEN
                and self._in_flight >= self.config.max_queue_depth)

    # ── seq/t_ns: peek/commit (decision (a)) ──────────────────────────

    def peek(self) -> Tuple[int, int]:
        """(seq, t_ns) for the next frame WITHOUT advancing. Raises unless the
        session is OPEN."""
        if not self.can_emit():
            raise RuntimeError(f"peek(): cannot emit in state {self.state.value}")
        return self._seq, self._t_ns

    def commit(self) -> None:
        """Atomically advance seq (+1, I1) and t_ns (+dur_ns, I2). Call only
        after the frame for the peeked (seq, t_ns) was successfully encoded;
        a failed encode leaves both untouched (no client-visible gap)."""
        if not self.can_emit():
            raise RuntimeError(f"commit(): cannot emit in state {self.state.value}")
        self._seq += 1
        self._t_ns += self.config.dur_ns

    def next_seq(self) -> int:
        """Deprecated advance-then-return path (pre-peek/commit). Kept for
        compatibility with older call sites; new code must use peek()/commit()
        around encode()."""
        seq, _ = self.peek()
        self.commit()
        return seq

    # ── read-only views (metrics must not touch privates) ─────────────

    def current_t_ns(self) -> int:
        return self._t_ns

    def next_seq_num(self) -> int:
        """Public read of the next seq (the value peek() would return)."""
        return self._seq

    # ── queue accounting ──────────────────────────────────────────────────

    def on_chunk_enqueued(self) -> None:
        self._in_flight += 1

    def on_chunk_sent(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)

    def queue_depth(self) -> int:
        return self._in_flight
