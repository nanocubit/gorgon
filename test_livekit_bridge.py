"""test_livekit_bridge.py — wire-side tests for livekit_bridge.py.

Runs WITHOUT the livekit SDK: exercises AudioToBlendshapeConverter,
BreathingIdleGenerator (incl. the lazy speech→idle crossfade),
RecordingModelQueue and GorgonLiveKitBridge against a fake data layer, then
decodes every published frame with the facecontract canon and asserts the
locked invariants (I1 seq+1, I2 t_ns step, I3 one-hot, I4 CRC+residue,
I5 seq=0, I6 bs_count).

Key behaviours pinned here:
  - converter buffers exactly one chunk period (6400 bytes @ 24kHz,
    fps=30, cs=4) — the chat draft's `chunk_size*512`/`~17ms` was wrong;
  - the idle crossfade arms LAZILY inside the generator: gorgon_glue.tick()
    clears Gorgon._last_model_payload on the SPEAKING→IDLE boundary tick
    BEFORE the idle generator runs, so the tail must come from
    RecordingModelQueue.last_popped (the last RENDERED chunk);
  - the fade is one-shot per utterance (mark_utterance_start re-arms).
"""
import json
import math
import struct
import unittest
import zlib

import facecontract_v141 as fc
from livekit_bridge import (
    AudioToBlendshapeConverter,
    BreathingIdleGenerator,
    DequeModelQueue,
    GorgonLiveKitBridge,
    IdleGeneratorConfig,
    RecordingModelQueue,
    TOPIC_FRAMES,
    TOPIC_STATE,
    TOPIC_TRIGGERS,
)


class FakeDataLayer:
    """Duck-typed publish_data sink (mirrors the LiveKit SDK surface)."""

    def __init__(self):
        self.published = []

    def publish_data(self, data, topic="", reliable=True):
        self.published.append((topic, data))


def _loud_tone(samples: int, amplitude: int = 20000) -> bytes:
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * i / 40)))
        for i in range(samples))


class TestAudioToBlendshapeConverter(unittest.TestCase):
    def test_chunk_period_alignment(self):
        conv = AudioToBlendshapeConverter(bs_count=32, fps=30, chunk_size=4)
        self.assertEqual(conv._target_bytes, 6400)

    def test_buffering(self):
        conv = AudioToBlendshapeConverter(bs_count=32)
        self.assertIsNone(conv.feed_audio(b"\x00" * 3200))   # half buffer
        chunk = conv.feed_audio(b"\x00" * 3200)              # completes it
        self.assertIsNotNone(chunk)
        self.assertEqual(len(chunk), 128)

    def test_silence_to_zero_jaw(self):
        conv = AudioToBlendshapeConverter(bs_count=32)
        chunk = conv.feed_audio(b"\x00" * 6400)
        jaw_idx = fc.NAME_TO_INDEX["jawOpen"]
        self.assertEqual(chunk[jaw_idx], 0.0)
        self.assertEqual(chunk[fc.NAME_TO_INDEX["mouthSmileLeft"]], 0.1)

    def test_loud_tone_caps_jaw(self):
        conv = AudioToBlendshapeConverter(bs_count=32)
        chunk = conv.feed_audio(_loud_tone(3200))
        jaw_idx = fc.NAME_TO_INDEX["jawOpen"]
        self.assertAlmostEqual(chunk[jaw_idx], 0.8, places=6)

    def test_flush_partial(self):
        conv = AudioToBlendshapeConverter(bs_count=32)
        self.assertIsNone(conv.feed_audio(b"\x00" * 100))
        chunk = conv.flush()
        self.assertIsNotNone(chunk)
        self.assertEqual(len(chunk), 128)
        self.assertIsNone(conv.flush())


class TestBreathingIdleGenerator(unittest.TestCase):
    def test_breathing_rows(self):
        gen = BreathingIdleGenerator(IdleGeneratorConfig(bs_count=32))
        p = gen.next()
        self.assertEqual(len(p), 128)
        jaw_idx = fc.NAME_TO_INDEX["jawOpen"]
        jaw = [p[f * 32 + jaw_idx] for f in range(4)]
        for v in jaw:
            self.assertGreater(v, 0.0)
            self.assertLess(v, 0.06)

    def test_lazy_crossfade_from_provider(self):
        gen = BreathingIdleGenerator(IdleGeneratorConfig(bs_count=32))
        gen.set_tail_provider(lambda: [0.8] * 128)
        p = gen.next()
        jaw_idx = fc.NAME_TO_INDEX["jawOpen"]
        self.assertGreater(p[jaw_idx], 0.5)      # pulled toward the tail
        self.assertEqual(gen._fade_left, 2)      # 6 units − 4 sub-frames
        self.assertTrue(gen._faded_this_utterance)

    def test_fade_one_shot_per_utterance(self):
        gen = BreathingIdleGenerator(IdleGeneratorConfig(bs_count=32))
        gen.set_tail_provider(lambda: [0.8] * 128)
        gen.next()
        for _ in range(5):
            gen.next()
        jaw_idx = fc.NAME_TO_INDEX["jawOpen"]
        self.assertEqual(gen._fade_left, 0)
        self.assertLess(gen.next()[jaw_idx], 0.06)   # pure breathing again

    def test_mark_utterance_start_rearms(self):
        gen = BreathingIdleGenerator(IdleGeneratorConfig(bs_count=32))
        gen.set_tail_provider(lambda: [0.8] * 128)
        gen.next()
        gen.mark_utterance_start()
        self.assertFalse(gen._faded_this_utterance)
        self.assertGreater(gen.next()[fc.NAME_TO_INDEX["jawOpen"]], 0.5)

    def test_explicit_on_speech_end(self):
        gen = BreathingIdleGenerator(IdleGeneratorConfig(bs_count=32))
        gen.on_speech_end([0.8] * 128)
        self.assertGreater(gen.next()[fc.NAME_TO_INDEX["jawOpen"]], 0.5)

    def test_malformed_tail_disarms(self):
        gen = BreathingIdleGenerator(IdleGeneratorConfig(bs_count=32))
        gen.on_speech_end([0.5] * 7)             # wrong length
        self.assertEqual(gen._fade_left, 0)
        gen.on_speech_end(None)
        self.assertEqual(gen._fade_left, 0)


class TestModelQueues(unittest.TestCase):
    def test_deque_fifo_and_maxlen(self):
        q = DequeModelQueue(maxlen=3)
        self.assertIsNone(q.pop())
        for v in ([1], [2], [3], [4]):
            q.push(v)
        self.assertEqual([q.pop(), q.pop(), q.pop(), q.pop()],
                         [[2], [3], [4], None])

    def test_recording_queue_remembers_last_popped(self):
        q = RecordingModelQueue(maxlen=60)
        self.assertIsNone(q.last_popped)
        q.push([1] * 128)
        q.push([2] * 128)
        self.assertEqual(q.pop(), [1] * 128)
        self.assertEqual(q.last_popped, [1] * 128)
        self.assertEqual(q.pop(), [2] * 128)
        self.assertEqual(q.last_popped, [2] * 128)   # survives queue emptying
        self.assertIsNone(q.pop())                    # stays at last rendered


class TestBridgeEndToEnd(unittest.TestCase):
    def _make_bridge(self, bs_count=32):
        layer = FakeDataLayer()
        bridge = GorgonLiveKitBridge(room=object(), bs_count=bs_count, fps=30,
                                     chunk_size=4, session_id="sess-1",
                                     user_external_id="user-42",
                                     data_layer=layer)
        return bridge, layer

    def _run_ticks(self, bridge, count=24):
        frames = []
        now = 0
        for _ in range(count):
            bridge.tick_once(now)
            now += bridge.cfg.dur_ns // 1000
            while True:
                f = bridge.gorgon.take_frame()
                if f is None:
                    break
                frames.append(f)
        return frames

    def test_invariants_on_wire(self):
        bridge, _ = self._make_bridge()
        bridge.initialize()
        bridge.on_user_started_speaking()
        for _ in range(5):
            bridge.on_tts_audio(_loud_tone(3200))
        bridge.on_agent_speech_committed()
        frames = self._run_ticks(bridge)

        self.assertGreaterEqual(len(frames), 10)
        cfg = bridge.cfg
        prev_seq, prev_t = None, None
        for i, f in enumerate(frames):
            d = fc.decode(f)                     # full canon validation
            self.assertEqual(d["bs_count"], 32)          # I6
            self.assertEqual(len(d["payload"]), 128)
            self.assertEqual(zlib.crc32(f) & 0xFFFFFFFF, fc.CRC_RESIDUE)
            self.assertIn(d["flags"], (fc.FLAG_IDLE, fc.FLAG_SPEAKING))  # I3
            if prev_seq is not None:
                self.assertEqual(d["seq"], prev_seq + 1)          # I1
                self.assertEqual(d["t_ns"] - prev_t, cfg.dur_ns)  # I2
            prev_seq, prev_t = d["seq"], d["t_ns"]
        self.assertEqual(fc.decode(frames[0])["seq"], 0)          # I5

    def test_speaking_then_idle_flags(self):
        bridge, _ = self._make_bridge()
        bridge.initialize()
        bridge.on_user_started_speaking()
        for _ in range(5):
            bridge.on_tts_audio(_loud_tone(3200))
        bridge.on_agent_speech_committed()
        flags = [fc.decode(f)["flags"] for f in self._run_ticks(bridge)]
        self.assertIn(fc.FLAG_SPEAKING, flags)
        self.assertIn(fc.FLAG_IDLE, flags)

    def test_idle_crossfade_starts_from_tail(self):
        bridge, _ = self._make_bridge()
        bridge.initialize()
        bridge.on_user_started_speaking()
        for _ in range(5):
            bridge.on_tts_audio(_loud_tone(3200))
        bridge.on_agent_speech_committed()
        frames = self._run_ticks(bridge)
        flags = [fc.decode(f)["flags"] for f in frames]
        last_spk = max(i for i, f in enumerate(flags) if f == fc.FLAG_SPEAKING)
        first_idle = fc.decode(frames[last_spk + 1])
        jaw_idx = fc.NAME_TO_INDEX["jawOpen"]
        self.assertGreater(first_idle["payload"][jaw_idx], 0.15)

    def test_topics_and_json_shapes(self):
        bridge, layer = self._make_bridge()
        bridge.initialize()
        self.assertTrue(any(t == TOPIC_STATE for t, _ in layer.published))
        bridge.on_trigger("curious")
        trig = [d for t, d in layer.published if t == TOPIC_TRIGGERS]
        self.assertEqual(len(trig), 1)
        doc = json.loads(trig[0])
        self.assertEqual(doc["type"], "trigger_reaction")
        self.assertEqual(doc["reaction"], "curious")
        self.assertEqual(doc["session_id"], "sess-1")
        states = [json.loads(d) for t, d in layer.published
                  if t == TOPIC_STATE]
        for s in states:
            self.assertEqual(s["type"], "state")
            self.assertIn("seq_next", s)
            self.assertIn("speaking", s)


class TestPremiumLevel(unittest.TestCase):
    def test_bs72_pipeline(self):
        layer = FakeDataLayer()
        bridge = GorgonLiveKitBridge(room=object(), bs_count=72, fps=30,
                                     chunk_size=4, data_layer=layer)
        bridge.initialize()
        conv = AudioToBlendshapeConverter(bs_count=72, fps=30, chunk_size=4)
        chunk = conv.feed_audio(b"\x00" * 6400)
        self.assertEqual(len(chunk), 288)
        bridge.on_user_started_speaking()
        bridge.gorgon.on_model_chunk(bridge._now_us(), chunk)
        bridge.on_agent_speech_committed()
        bridge.tick_once(0)
        frame = bridge.gorgon.take_frame()
        self.assertIsNotNone(frame)
        d = fc.decode(frame)
        self.assertEqual(d["bs_count"], 72)
        self.assertEqual(len(d["payload"]), 288)
        self.assertEqual(zlib.crc32(frame) & 0xFFFFFFFF, fc.CRC_RESIDUE)


if __name__ == "__main__":
    unittest.main()
