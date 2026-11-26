"""
test_gorgon_glue.py — integration tests for Gorgon (gorgon_glue) with fake
IdleGenerator / ModelQueue, driving tick() through the §5 scenarios and
asserting the invariants I1–I9 on the emitted wire frames. Every claim is
checked against the bytes the client would actually receive: each emitted
frame is decoded back through facecontract_v141.decode(), so CRC validity
(I4), seq continuity (I1/I5), t_ns stepping (I2/I9), one-hot flags (I3) and
payload length (I6) are all verified on the wire representation.

Run: python -m unittest test_gorgon_glue -v
"""
import dataclasses
import logging
import struct
import unittest
from typing import Dict, List, Optional

import gorgon_glue
from facecontract_v141 import FLAG_IDLE, FLAG_SPEAKING, decode
from gorgon_glue import Gorgon
from gorgon_state import (
    GorgonConfig, SessionState, SessionStateMachine, UtteranceState,
)

logging.getLogger("gorgon").setLevel(logging.CRITICAL)

BS_COUNT = 4
IDLE_PAYLOAD = [0.03, 0.02, 0.05, 0.01, 0.0, 0.04, 0.02, 0.01] * 2  # 16 floats


def make_payload(seed: int, n: int = BS_COUNT * 4) -> List[float]:
    x = seed & 0x7FFFFFFF
    out = []
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((x % 10001) / 10000.0)
    return out


def P(seed: int) -> List[float]:
    return make_payload(seed)


def f32_list(values: List[float]) -> List[float]:
    """Widen-to-f32 exactly like the wire does, for exact payload asserts."""
    return [struct.unpack("<f", struct.pack("<f", v))[0] for v in values]


class FakeIdleGen:
    def __init__(self, n: int, pattern: Optional[List[float]] = None):
        self.n = n
        self.pattern = list(pattern) if pattern else None

    def next(self) -> List[float]:
        return list(self.pattern) if self.pattern else [0.03] * self.n


class FakeQueue:
    """Minimal ModelQueue: push + pop (protocol-compliant, no fallback)."""

    def __init__(self) -> None:
        self._q: List[List[float]] = []

    def push(self, payload: List[float]) -> None:
        self._q.append(payload)

    def pop(self) -> Optional[List[float]]:
        return self._q.pop(0) if self._q else None

    def depth(self) -> int:
        return len(self._q)


class Driver:
    """tick + take_frame per step (transport always drains → no backpressure)."""

    def __init__(self, g: Gorgon):
        self.g = g
        self.now = 0
        self.frames: List[bytes] = []
        g.on_ws_open(0)

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.g.tick(self.now)
            frame = self.g.take_frame()
            if frame is not None:
                self.frames.append(frame)
            self.now += self.g.cfg.dur_us


class GorgonGlueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = GorgonConfig()  # fps=30, chunk=4, hangover=500ms, maxq=8
        self.dur_ns = self.cfg.dur_ns

    def _make_gorgon(self, cfg: Optional[GorgonConfig] = None,
                     bs: int = BS_COUNT) -> Gorgon:
        cfg = cfg or self.cfg
        self.q = FakeQueue()
        idle = FakeIdleGen(bs * cfg.chunk_size,
                           pattern=IDLE_PAYLOAD if bs == BS_COUNT else None)
        return Gorgon(cfg, bs, idle, self.q)

    # ── shared assertion: I1–I9 on the wire stream ────────────────────────

    def assert_stream_invariants(self, frames: List[Dict],
                                 cfg: GorgonConfig) -> None:
        """I1/I5 (seq strict +1 from 0), I2/I9 (t_ns strict step), I3 (one-hot
        flags), I4 (decode() implies CRC valid), I6 (payload length)."""
        self.assertTrue(frames)
        for i, d in enumerate(frames):
            self.assertEqual(d["seq"], i, f"I1/I5 violation at position {i}")
            self.assertEqual(d["t_ns"], i * cfg.dur_ns,
                             f"I2/I9 violation at position {i}")
            self.assertIn(d["flags"], (FLAG_IDLE, FLAG_SPEAKING),
                          f"I3 violation at {i}")
            self.assertEqual(len(d["payload"]), BS_COUNT * cfg.chunk_size,
                             f"I6 violation at {i}")

    # ── §5 scenarios ──────────────────────────────────────────────────

    def test_01_pure_idle_session(self):
        g = self._make_gorgon()
        d = Driver(g)
        d.step(10)
        decoded = [decode(f) for f in d.frames]
        self.assertEqual(len(decoded), 10)
        self.assert_stream_invariants(decoded, self.cfg)
        self.assertTrue(all(x["flags"] == FLAG_IDLE for x in decoded))
        self.assertEqual(g.chunks_dropped, 0)
        self.assertEqual(g.metrics()["chunks_emitted"], 10)

    def test_02_full_utterance_hold_last_tail(self):
        g = self._make_gorgon()
        d = Driver(g)
        d.step(3)                       # seq 0–2: idle
        g.on_vad_start(d.now)           # → BUFFERING
        d.step(2)                       # seq 3–4: idle (no model output yet)
        g.on_model_chunk(d.now, P(1))   # first chunk → on_model_ready → STREAMING
        g.on_model_chunk(d.now, P(2))
        g.on_model_chunk(d.now, P(3))
        d.step(3)                       # seq 5–7: P1..P3
        g.on_model_chunk(d.now, P(4))
        g.on_model_chunk(d.now, P(5))
        d.step(2)                       # seq 8–9: P4..P5
        g.on_model_chunk(d.now, P(6))
        d.step(1)                       # seq 10: P6
        g.on_vad_end(d.now)             # → SPEECH_HANGOVER (500 ms)
        d.step(4)                       # seq 11–14: hold-last tail = P6
        d.step(3)                       # seq 15–17: hangover expired → idle
        decoded = [decode(f) for f in d.frames]
        self.assertEqual(len(decoded), 18)
        self.assert_stream_invariants(decoded, self.cfg)
        self.assertEqual(
            [x["flags"] for x in decoded],
            [FLAG_IDLE] * 5 + [FLAG_SPEAKING] * 10 + [FLAG_IDLE] * 3)
        payloads = [x["payload"] for x in decoded]
        self.assertEqual(payloads[5:11], [f32_list(P(k)) for k in range(1, 7)])
        self.assertEqual(payloads[11:15], [f32_list(P(6))] * 4)   # hold-last tail
        self.assertEqual(payloads[15:18], [f32_list(IDLE_PAYLOAD)] * 3)
        self.assertEqual(self.q.depth(), 0)   # I7: nothing left behind

    def test_03_backpressure_drops_idle_only_i7(self):
        cfg = dataclasses.replace(self.cfg, max_queue_depth=2)
        g = self._make_gorgon(cfg)
        g.on_ws_open(0)
        now = 0
        for _ in range(2):              # transport stalled: emit without take
            g.tick(now); now += g.cfg.dur_us
        for _ in range(3):              # backpressure → idle chunks dropped
            g.tick(now); now += g.cfg.dur_us
        self.assertEqual(g.chunks_dropped, 3)
        self.assertEqual(len(g._send_queue), 2)
        g.on_vad_start(now)             # → BUFFERING (stale-guard no longer eats chunks)
        for seed in (1, 2, 3):
            g.on_model_chunk(now, P(seed))   # first chunk → on_model_ready → STREAMING
        for _ in range(3):              # model chunks are NEVER dropped (I7)
            g.tick(now); now += g.cfg.dur_us
        self.assertEqual(g.chunks_dropped, 3)
        self.assertEqual(self.q.depth(), 0)
        frames = [g.take_frame() for _ in range(5)]
        self.assertTrue(all(f is not None for f in frames))
        decoded = [decode(f) for f in frames]
        self.assert_stream_invariants(decoded, cfg)   # no gap on the wire
        self.assertEqual([x["seq"] for x in decoded], [0, 1, 2, 3, 4])
        self.assertEqual(decoded[2]["payload"], f32_list(P(1)))
        self.assertEqual(decoded[4]["payload"], f32_list(P(3)))
        g.tick(now); now += g.cfg.dur_us              # drained → recovers
        self.assertIsNotNone(g.take_frame())

    def test_04_encode_failure_leaves_no_seq_gap(self):
        """Decision (a) peek/commit: encode failure must NOT advance seq/t_ns —
        the next emitted frame re-uses the same seq, so the client never sees a
        gap; the session also must not be force-failed (improvement over (b))."""
        g = self._make_gorgon()
        d = Driver(g)
        original = gorgon_glue.encode
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("injected encode failure")
            return original(**kwargs)

        gorgon_glue.encode = flaky
        try:
            d.step(1)                    # seq 0 emitted
            d.step(1)                    # encode raises → nothing emitted
            self.assertEqual(len(d.frames), 1)
            self.assertEqual(g.session.state, SessionState.OPEN)
            self.assertEqual(g.session.next_seq_num(), 1)
            self.assertEqual(g.session.current_t_ns(), self.cfg.dur_ns)
            self.assertEqual(g.chunks_encode_failed, 1)
            d.step(1)                    # same seq re-used → stream stays dense
        finally:
            gorgon_glue.encode = original
        decoded = [decode(f) for f in d.frames]
        self.assertEqual(len(decoded), 2)
        self.assert_stream_invariants(decoded, self.cfg)
        self.assertEqual([x["seq"] for x in decoded], [0, 1])

    def test_05_stale_model_chunk_discarded_in_idle(self):
        g = self._make_gorgon()
        d = Driver(g)
        d.step(1)
        stale = P(9)
        g.on_model_chunk(d.now, stale)   # IDLE → discarded, not queued
        self.assertEqual(self.q.depth(), 0)
        g.on_vad_start(d.now)
        g.on_model_chunk(d.now, P(1))
        d.step(1)
        decoded = [decode(f) for f in d.frames]
        self.assert_stream_invariants(decoded, self.cfg)
        self.assertEqual(decoded[1]["payload"], f32_list(P(1)))
        for x in decoded:
            self.assertNotEqual(x["payload"], f32_list(stale))  # never resurfaces

    def test_06_model_chunk_length_mismatch(self):
        g = self._make_gorgon()
        d = Driver(g)
        bad = [0.1] * 15
        g.on_model_chunk(d.now, bad)     # IDLE: discarded before length check
        self.assertEqual(self.q.depth(), 0)
        g.on_vad_start(d.now)            # BUFFERING
        with self.assertRaises(ValueError):
            g.on_model_chunk(d.now, bad)

    def test_07_streaming_empty_queue_falls_back_to_idle(self):
        g = self._make_gorgon()
        d = Driver(g)
        d.step(1)
        g.on_vad_start(d.now)
        g.on_model_chunk(d.now, P(1))
        d.step(1)                        # P1 (seq 1)
        d.step(1)                        # queue empty in STREAMING → idle payload,
                                         # but flags stay SPEAKING (is_speaking)
        decoded = [decode(f) for f in d.frames]
        self.assert_stream_invariants(decoded, self.cfg)
        self.assertEqual(decoded[1]["payload"], f32_list(P(1)))
        self.assertEqual(decoded[2]["flags"], FLAG_SPEAKING)
        self.assertEqual(decoded[2]["payload"], f32_list(IDLE_PAYLOAD))

    def test_08_network_fail_is_fail_closed(self):
        g = self._make_gorgon()
        g.on_ws_open(0)
        now = 0
        for _ in range(3):               # emit without take → 3 queued
            g.tick(now); now += g.cfg.dur_us
        g.on_network_fail()
        self.assertEqual(g.session.state, SessionState.FAILED)
        before = len(g._send_queue)
        g.tick(now)                      # FAILED → can_emit False → nothing new
        self.assertEqual(len(g._send_queue), before)
        frames = [g.take_frame() for _ in range(3)]
        self.assertTrue(all(f is not None for f in frames))
        self.assertIsNone(g.take_frame())
        self.assertEqual(g.session.queue_depth(), 0)

    def test_09_client_stop_drains_then_stops(self):
        g = self._make_gorgon()
        g.on_ws_open(0)
        now = 0
        for _ in range(2):
            g.tick(now); now += g.cfg.dur_us
        self.assertIsNotNone(g.take_frame())     # in_flight = 1
        g.on_client_stop()
        self.assertEqual(g.session.state, SessionState.DRAINING)
        g.tick(now)                              # no new frames while draining
        self.assertEqual(len(g._send_queue), 1)
        self.assertIsNotNone(g.take_frame())     # buffered frame still delivered
        self.assertIsNone(g.take_frame())

    # ── unit-level ────────────────────────────────────────────────────────

    def test_10_session_peek_commit_unit(self):
        sm = SessionStateMachine(self.cfg)
        with self.assertRaises(RuntimeError):
            sm.peek()                            # CLOSED
        with self.assertRaises(RuntimeError):
            sm.commit()
        sm.open(5_000_000)
        self.assertEqual(sm.peek(), (0, 5_000_000))
        self.assertEqual(sm.peek(), (0, 5_000_000))          # peek never advances
        sm.commit()
        self.assertEqual(sm.peek(), (1, 5_000_000 + self.dur_ns))
        for _ in range(5):
            sm.commit()
        self.assertEqual(sm.next_seq_num(), 6)
        self.assertEqual(sm.current_t_ns(), 5_000_000 + 6 * self.dur_ns)
        self.assertEqual(sm.next_seq(), 6)                   # deprecated, atomic
        self.assertEqual(sm.next_seq_num(), 7)
        sm.on_client_stop()
        with self.assertRaises(RuntimeError):
            sm.peek()                                        # DRAINING
        with self.assertRaises(RuntimeError):
            sm.commit()
        with self.assertRaises(RuntimeError):
            sm.open(0)                                       # double open

    def test_11_bs_count_must_be_supported(self):
        with self.assertRaises(ValueError):
            self._make_gorgon(bs=3)              # I6
        g = self._make_gorgon(bs=1)
        g.on_ws_open(0)
        g.tick(0)
        d = decode(g.take_frame())
        self.assertEqual(d["bs_count"], 1)
        self.assertEqual(len(d["payload"]), self.cfg.chunk_size)

    def test_12_metrics_use_public_session_api(self):
        g = self._make_gorgon()
        d = Driver(g)
        d.step(3)
        m = g.metrics()
        self.assertEqual(m["seq_next"], 3)                   # via next_seq_num()
        self.assertEqual(m["t_ns_next"], 3 * self.dur_ns)
        self.assertEqual(m["chunks_emitted"], 3)
        self.assertEqual(m["ticks"], 3)
        self.assertEqual(m["queue_depth"], 0)
        self.assertEqual(m["send_queue"], 0)
        self.assertEqual(m["state_utterance"], UtteranceState.IDLE_STREAMING.value)
        self.assertEqual(m["state_session"], SessionState.OPEN.value)
        self.assertEqual(m["chunks_encode_failed"], 0)


if __name__ == "__main__":
    unittest.main()
