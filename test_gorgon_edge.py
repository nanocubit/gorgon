"""
test_gorgon_edge.py — edge cases for the Gorgon state machines, adapted to
the LOCKED implementation in this repository (the canon per thread
decision; the draft's current_seq_num()/SessionState.BACKPRESSURE/
SPEECH_BUFFERING names do not exist here and were mapped to the real API:
next_seq_num(), SessionState.OPEN + should_drop_idle(), BUFFERING).

  E1. VAD_START during HANGOVER resumes; the old deadline cannot ghost-fire
      (checked over 5 s after resume); second hangover gets a fresh deadline;
      resume after model_done goes to BUFFERING, not STREAMING.
  E2. model_chunk after model_done: rejected via accepts_model_chunks();
      glue-level: a late push does not resurrect the tail.
  E3. reconnect mid-utterance: new session seq=0, fresh t_ns epoch, queue not
      inherited (unit + end-to-end through Gorgon).
  E4. 1-hour accumulation: exact integer step, no drift (27 000 chunks);
      dur_ns is the theoretical value, NOT dur_us * 1000.
  E5. model_chunk during HANGOVER: rejected; chunks queued BEFORE hangover
      still drain; the tail stays authoritative (glue-level).

Run: python -m unittest test_gorgon_edge -v
"""
import unittest
from typing import List

from gorgon_state import (
    GorgonConfig, UtteranceState, UtteranceStateMachine,
    SessionState, SessionStateMachine,
)
from facecontract_v141 import FLAG_IDLE, FLAG_SPEAKING, decode
from gorgon_glue import Gorgon
from test_gorgon_glue import (
    BS_COUNT, IDLE_PAYLOAD, FakeIdleGen, FakeQueue, P, f32_list,
)


def ms(n: int) -> int:
    return n * 1000


def tick_until_us(sm: UtteranceStateMachine, start_us: int, end_us: int,
                  step_us: int = 33_333) -> int:
    t = start_us
    while t < end_us:
        sm.tick(t)
        t += step_us
    return t


def assert_stream(frames: List[bytes], cfg: GorgonConfig,
                  base_t_ns: int = 0) -> None:
    """I1/I5 + I2/I9 + I3 + I4 on the emitted wire stream."""
    for i, f in enumerate(frames):
        d = decode(f)
        assert d["seq"] == i, f"I1/I5 violation at {i}: seq={d['seq']}"
        assert d["t_ns"] == base_t_ns + i * cfg.dur_ns, f"I2/I9 violation at {i}"
        assert d["flags"] in (FLAG_IDLE, FLAG_SPEAKING), f"I3 violation at {i}"


def make_gorgon(cfg: GorgonConfig) -> Gorgon:
    return Gorgon(cfg, BS_COUNT, FakeIdleGen(16, pattern=IDLE_PAYLOAD), FakeQueue())


def emit(g: Gorgon, n: int, now_us: int = 0):
    """tick + take_frame n times; returns (frames, now_us after)."""
    frames = []
    for _ in range(n):
        g.tick(now_us)
        f = g.take_frame()
        if f is not None:
            frames.append(f)
        now_us += g.cfg.dur_us
    return frames, now_us


# ═════════════════════════════════════════════════════════════════════════
# E1. VAD_START in HANGOVER: deadline cleared, no ghost expiry
# ═════════════════════════════════════════════════════════════════════════

class TestE1_HangoverDeadlineCleared(unittest.TestCase):
    """STREAMING → VAD_END → HANGOVER (deadline set) → VAD_START → resume.
    The OLD deadline must never fire after resume."""

    def _reach_hangover(self, sm: UtteranceStateMachine) -> None:
        sm.on_vad_start(ms(0))
        tick_until_us(sm, ms(0), ms(100))
        sm.on_model_ready(ms(200))
        self.assertEqual(sm.state, UtteranceState.SPEECH_STREAMING)
        sm.on_vad_end(ms(500))
        self.assertEqual(sm.state, UtteranceState.SPEECH_HANGOVER)

    def test_resume_cancels_old_deadline(self):
        sm = UtteranceStateMachine(GorgonConfig(hangover_ms=500))
        self._reach_hangover(sm)
        sm.on_vad_start(ms(700))          # resume at 300 ms into the hangover
        self.assertEqual(sm.state, UtteranceState.SPEECH_STREAMING,
                         "resume with model still producing must go STREAMING")
        self.assertIsNone(sm.hangover_start_us, "deadline must be cleared")
        tick_until_us(sm, ms(700), ms(1200))   # past the OLD expiry (t=1000)
        self.assertEqual(sm.state, UtteranceState.SPEECH_STREAMING,
                         "old hangover deadline fired after resume")

    def test_no_ghost_expiry_5s_after_resume(self):
        sm = UtteranceStateMachine(GorgonConfig(hangover_ms=500))
        self._reach_hangover(sm)
        sm.on_vad_start(ms(700))
        tick_until_us(sm, ms(700), ms(700) + 5_000_000)   # 5 seconds
        self.assertEqual(sm.state, UtteranceState.SPEECH_STREAMING,
                         "ghost expiry 5 s after resume")

    def test_second_hangover_after_resume(self):
        sm = UtteranceStateMachine(GorgonConfig(hangover_ms=500))
        self._reach_hangover(sm)
        sm.on_vad_start(ms(700))
        sm.on_vad_end(ms(1500))           # fresh hangover, deadline 2000
        self.assertEqual(sm.state, UtteranceState.SPEECH_HANGOVER)
        tick_until_us(sm, ms(1500), ms(1800))
        self.assertEqual(sm.state, UtteranceState.SPEECH_HANGOVER)
        tick_until_us(sm, ms(1800), ms(2100))
        self.assertEqual(sm.state, UtteranceState.IDLE_STREAMING)

    def test_resume_after_model_done_goes_buffering(self):
        sm = UtteranceStateMachine(GorgonConfig(hangover_ms=500))
        self._reach_hangover(sm)
        sm.on_model_done(ms(600))         # EOS seen; model won't produce more
        sm.on_vad_start(ms(700))
        self.assertEqual(sm.state, UtteranceState.BUFFERING,
                         "after model_done a resume must wait for fresh output")
        sm.on_model_ready(ms(900))        # new utterance arrives
        self.assertEqual(sm.state, UtteranceState.SPEECH_STREAMING)


# ═════════════════════════════════════════════════════════════════════════
# E2. model_chunk after model_done: rejected, tail not resurrected
# ═════════════════════════════════════════════════════════════════════════

class TestE2_ModelChunkAfterDone(unittest.TestCase):
    """Bug scenario: model adapter emits a chunk after done (protocol
    violation). Without the HANGOVER rejection, the chunk would be pushed,
    popped instead of the tail, and become the new tail — the 'resurrected'
    wrong frame."""

    def _to_streaming(self) -> UtteranceStateMachine:
        sm = UtteranceStateMachine(GorgonConfig())
        sm.on_vad_start(ms(0))
        sm.on_model_ready(ms(200))
        return sm

    def test_accepts_in_streaming(self):
        self.assertTrue(self._to_streaming().accepts_model_chunks())

    def test_rejects_after_done(self):
        sm = self._to_streaming()
        sm.on_model_done(ms(500))
        self.assertEqual(sm.state, UtteranceState.SPEECH_HANGOVER)
        self.assertFalse(sm.accepts_model_chunks(),
                         "HANGOVER after model_done must not accept new chunks")

    def test_rejects_in_idle(self):
        sm = UtteranceStateMachine(GorgonConfig())
        self.assertFalse(sm.accepts_model_chunks())

    def test_glue_chunk_after_done_does_not_resurrect_tail(self):
        cfg = GorgonConfig()
        g = make_gorgon(cfg)
        g.on_ws_open(0)
        frames, now = emit(g, 1)                    # seq 0: idle
        g.on_vad_start(now)
        for seed in (1, 2, 3):
            g.on_model_chunk(now, P(seed))          # first → ready → STREAMING
        new_frames, now = emit(g, 2, now)           # seq 1,2: P1, P2
        frames.extend(new_frames)
        g.on_model_done(now)                        # → HANGOVER; queue holds P3
        g.on_model_chunk(now, P(4))                 # late push — must be rejected
        self.assertEqual(g.model_queue.depth(), 1, "only P3 must remain queued")
        new_frames, now = emit(g, 2, now)           # seq 3: P3 drain, seq 4: tail P3
        frames.extend(new_frames)
        decoded = [decode(f) for f in frames]
        assert_stream(frames, cfg)
        payloads = [d["payload"] for d in decoded]
        self.assertEqual(payloads[0], f32_list(IDLE_PAYLOAD))
        self.assertEqual(payloads[1:3], [f32_list(P(1)), f32_list(P(2))])
        self.assertEqual(payloads[3:5], [f32_list(P(3)), f32_list(P(3))],
                         "drain of queued P3 then hold-last tail P3")
        for p in payloads:
            self.assertNotEqual(p, f32_list(P(4)), "late chunk must never surface")


# ═════════════════════════════════════════════════════════════════════════
# E3. Reconnect mid-utterance: seq=0, fresh t_ns epoch, queue not inherited
# ═════════════════════════════════════════════════════════════════════════

class TestE3_Reconnect(unittest.TestCase):
    def test_new_session_seq_zero_fresh_epoch(self):
        cfg = GorgonConfig()
        s1 = SessionStateMachine(cfg)
        s1.open(start_t_ns=1_000_000_000)
        for _ in range(10):
            s1.next_seq()
        self.assertEqual(s1.next_seq_num(), 10)
        self.assertEqual(s1.current_t_ns(), 1_000_000_000 + 10 * cfg.dur_ns)

        # reconnect = a NEW SessionStateMachine (Gorgon is per-WebSocket)
        s2 = SessionStateMachine(cfg)
        s2.open(start_t_ns=5_000_000_000)
        self.assertEqual(s2.next_seq_num(), 0)
        self.assertEqual(s2.current_t_ns(), 5_000_000_000)
        self.assertEqual(s2.queue_depth(), 0)

    def test_reconnect_does_not_inherit_queue(self):
        cfg = GorgonConfig()
        s1 = SessionStateMachine(cfg)
        s1.open(0)
        for _ in range(35):
            s1.next_seq()
            s1.on_chunk_enqueued()
        self.assertEqual(s1.queue_depth(), 35)
        self.assertTrue(s1.should_drop_idle())   # backpressure gate active

        s2 = SessionStateMachine(cfg)
        s2.open(0)
        self.assertEqual(s2.state, SessionState.OPEN)
        self.assertEqual(s2.queue_depth(), 0)
        self.assertFalse(s2.should_drop_idle())

    def test_reconnect_end_to_end_fresh_epoch(self):
        cfg = GorgonConfig()
        g1 = make_gorgon(cfg)
        g1.on_ws_open(0)
        frames1, _ = emit(g1, 5)
        d1 = [decode(f) for f in frames1]
        assert_stream(frames1, cfg)
        self.assertEqual([d["seq"] for d in d1], [0, 1, 2, 3, 4])
        self.assertEqual([d["t_ns"] for d in d1],
                         [i * cfg.dur_ns for i in range(5)])

        # old socket dies mid-utterance; new socket → NEW Gorgon instance
        g2 = make_gorgon(cfg)
        g2.on_ws_open(5_000_000_000)
        frames2, _ = emit(g2, 5)
        d2 = [decode(f) for f in frames2]
        assert_stream(frames2, cfg, base_t_ns=5_000_000_000)
        self.assertEqual([d["seq"] for d in d2], [0, 1, 2, 3, 4],
                         "seq must restart at 0 on the new session (I5)")
        self.assertEqual([d["t_ns"] for d in d2],
                         [5_000_000_000 + i * cfg.dur_ns for i in range(5)],
                         "t_ns must start from the NEW epoch, not continue")
        self.assertTrue(all(d["flags"] == FLAG_IDLE for d in d1 + d2))


# ═════════════════════════════════════════════════════════════════════════
# E4. 1-hour t_ns accumulation: exact step, no drift
# ═════════════════════════════════════════════════════════════════════════

class TestE4_LongRunTiming(unittest.TestCase):
    N_CHUNKS = 27_000          # 7.5 chunks/s * 3600 s = 1 hour
    HOUR_NS = 3_600_000_000_000

    def test_one_hour_exact(self):
        cfg = GorgonConfig()
        s = SessionStateMachine(cfg)
        s.open(start_t_ns=0)
        for _ in range(self.N_CHUNKS):
            s.next_seq()
        self.assertEqual(s.current_t_ns(), self.N_CHUNKS * cfg.dur_ns)
        self.assertEqual(s.current_t_ns(), 3_599_999_991_000,
                         "1h of t_ns must be the exact integer product")

    def test_dur_ns_is_exact_theoretical(self):
        cfg = GorgonConfig()
        theoretical = (cfg.chunk_size * 1_000_000_000) // cfg.fps  # 133_333_333
        self.assertEqual(cfg.dur_ns, theoretical,
                         "dur_ns must be chunk_size*1e9//fps, NOT dur_us*1000")
        self.assertLess(cfg.dur_us * 1000, cfg.dur_ns,
                        "dur_us must be the floor of dur_ns (advisory)")

    def test_drift_quantified(self):
        """Documents WHY clients must use t_ns deltas (PROTOCOL §4.2):
        server vs wall clock: 9 us/hour (negligible); a naive client using
        dur_us*1000 vs the server stream: ~9 ms/hour, ~216 ms/day, ~1.5 s/week."""
        cfg = GorgonConfig()
        naive_step = cfg.dur_us * 1000            # 133_333_000
        real_step = cfg.dur_ns                    # 133_333_333
        self.assertEqual(real_step - naive_step, 333)
        # server stream vs wall clock after 1 h
        self.assertEqual(self.HOUR_NS - self.N_CHUNKS * real_step, 9_000)
        # naive client vs server stream after 1 h / 24 h / 7 d
        self.assertEqual(self.N_CHUNKS * (real_step - naive_step), 8_991_000)
        self.assertEqual(648_000 * 333, 215_784_000)   # ~216 ms/day
        self.assertEqual(4_536_000 * 333, 1_510_488_000)  # ~1.5 s/week


# ═════════════════════════════════════════════════════════════════════════
# E5. model_chunk in HANGOVER: queued-before-done still drains; tail authoritative
# ═════════════════════════════════════════════════════════════════════════

class TestE5_ModelChunkInHangover(unittest.TestCase):
    """Distinguishes two things that must NOT be conflated:
    (a) chunks queued BEFORE hangover started → still drained (no speech cut);
    (b) chunks pushed DURING hangover → rejected (would replace the tail)."""

    def test_hangover_rejects_new_chunks(self):
        sm = UtteranceStateMachine(GorgonConfig())
        sm.on_vad_start(ms(0))
        sm.on_model_ready(ms(200))
        sm.on_vad_end(ms(500))
        self.assertEqual(sm.state, UtteranceState.SPEECH_HANGOVER)
        self.assertFalse(sm.accepts_model_chunks(),
                         "HANGOVER must reject model chunks — tail is authoritative")

    def test_glue_drain_then_tail(self):
        cfg = GorgonConfig()
        g = make_gorgon(cfg)
        g.on_ws_open(0)
        g.on_vad_start(0)
        g.on_model_chunk(0, P(1))
        g.on_model_chunk(0, P(2))
        frames, now = emit(g, 1)              # seq 0: P1; queue holds P2
        g.on_vad_end(now)                     # → HANGOVER while P2 still queued
        g.on_model_chunk(now, P(3))           # late push — rejected
        self.assertEqual(g.model_queue.depth(), 1)
        more, now = emit(g, 2)                # seq 1: P2 (drain), seq 2: tail P2
        frames.extend(more)
        decoded = [decode(f) for f in frames]
        assert_stream(frames, cfg)
        payloads = [d["payload"] for d in decoded]
        self.assertEqual(payloads, [f32_list(P(1)), f32_list(P(2)), f32_list(P(2))])
        self.assertTrue(all(p != f32_list(P(3)) for p in payloads),
                        "chunk pushed during HANGOVER must never surface")


if __name__ == "__main__":
    unittest.main(verbosity=2)
