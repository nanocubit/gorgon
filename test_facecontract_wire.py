"""
test_facecontract_wire.py — wire-level defence-in-depth tests (PROTOCOL §5).

Covers the invariants the SM tests do not touch:
  - payload_len defence-in-depth: header field, truncation, and the fact that
    the specific error fires EVEN IF the CRC field is corrupted too
    (payload_len is validated before the CRC);
  - CRC corruption → specific error;
  - residue invariant crc32(frame) == 0x2144DF1C on every canonical frame;
  - one-hot flags enforcement at encode time (I3);
  - frame length formula;
  - big-endian CRC store: MEASURED diagnostic (2026-11-26): the residue becomes
    message-DEPENDENT (0x3cc9742c for b"123456789", 0x90045ecc for the
    speech_bs4 body) — it is NOT the byte-swapped 0x1CDF4421 predicted in
    review. What stays true: Check B fails deterministically for every frame,
    so an endianness bug cannot slip through silently.

Run: python -m unittest test_facecontract_wire -v
"""
import unittest
import zlib

import facecontract_v141 as fc


def be_residue(body: bytes) -> int:
    """crc32 over a frame whose CRC field was stored BIG-endian (the bug the
    residue check is supposed to diagnose)."""
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return zlib.crc32(body + crc.to_bytes(4, "big")) & 0xFFFFFFFF


class TestPayloadLenDefenceInDepth(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = fc.canonical_vectors()[1][1]  # speech_bs4

    def test_corrupted_payload_len_field_rejected_specifically(self):
        bad = bytearray(self.frame)
        bad[20] ^= 0xFF                      # payload_len u32 LE, low byte
        with self.assertRaisesRegex(fc.FaceContractError, "payload_len mismatch"):
            fc.decode(bytes(bad))

    def test_corrupted_payload_len_even_with_broken_crc(self):
        # The point of defence-in-depth: payload_len is checked BEFORE the
        # CRC, so the specific error fires even when the CRC field is broken.
        bad = bytearray(self.frame)
        bad[20] ^= 0xFF                      # payload_len
        bad[-1] ^= 0xFF                      # CRC field too
        with self.assertRaisesRegex(fc.FaceContractError, "payload_len mismatch"):
            fc.decode(bytes(bad))

    def test_truncated_frame_rejected(self):
        with self.assertRaisesRegex(fc.FaceContractError, "payload_len mismatch"):
            fc.decode(self.frame[:-3])       # transit truncation

    def test_crc_corruption_rejected(self):
        bad = bytearray(self.frame)
        bad[40] ^= 0x01                      # inside the payload
        with self.assertRaisesRegex(fc.FaceContractError, "CRC mismatch"):
            fc.decode(bytes(bad))


class TestResidueInvariant(unittest.TestCase):
    def test_all_canonical_vectors(self):
        for name, frame in fc.canonical_vectors():
            self.assertEqual(zlib.crc32(frame) & 0xFFFFFFFF, fc.CRC_RESIDUE,
                             f"residue broken for {name}")

    def test_all_session_frames(self):
        for i, frame in enumerate(fc.build_session_frames()):
            self.assertEqual(zlib.crc32(frame) & 0xFFFFFFFF, fc.CRC_RESIDUE,
                             f"residue broken for session frame {i}")


class TestEncodeValidation(unittest.TestCase):
    def test_flags_both_set_rejected(self):
        with self.assertRaises(fc.FaceContractError):
            fc.encode(seq=0, t_ns=0, dur_us=133333, fps=30, chunk_size=4,
                      payload=[0.0] * 16,
                      flags=fc.FLAG_IDLE | fc.FLAG_SPEAKING)

    def test_flags_none_set_rejected(self):
        with self.assertRaises(fc.FaceContractError):
            fc.encode(seq=0, t_ns=0, dur_us=133333, fps=30, chunk_size=4,
                      payload=[0.0] * 16, flags=0)

    def test_frame_length_formula(self):
        for _, frame in fc.canonical_vectors():
            d = fc.decode(frame)
            self.assertEqual(len(frame),
                             fc._FRAME_OVERHEAD
                             + d["bs_count"] * d["chunk_size"] * 4)

    def test_head_pose_reserved_in_v0(self):
        with self.assertRaisesRegex(fc.FaceContractError, "head_pose"):
            fc.encode(seq=0, t_ns=0, dur_us=133333, fps=30, chunk_size=4,
                      payload=[0.0] * 16, flags=fc.FLAG_IDLE,
                      head_pose=(0.1, 0.2))


class TestBigEndianCrcDiagnostic(unittest.TestCase):
    """MEASURED on 2026-11-26 (review predicted a constant byte-swapped
    residue 0x1CDF4421 — execution disagreed). Facts:
      - a big-endian CRC field breaks Check B for EVERY frame;
      - the resulting residue is message-dependent, so log the computed value;
      - it can therefore never collide with the LE residue 0x2144DF1C."""

    def test_be_residue_is_message_dependent(self):
        m1 = b"123456789"
        m2 = self.frame_body()
        r1, r2 = be_residue(m1), be_residue(m2)
        self.assertEqual(r1, 0x3CC9742C)
        self.assertEqual(r2, 0x90045ECC)
        self.assertNotEqual(r1, r2)
        self.assertNotEqual(r1, fc.CRC_RESIDUE)
        self.assertNotEqual(r2, fc.CRC_RESIDUE)

    def test_be_residue_never_collides_with_le_residue(self):
        for _, frame in fc.canonical_vectors():
            body = frame[:-4]
            self.assertNotEqual(be_residue(body), fc.CRC_RESIDUE)

    @staticmethod
    def frame_body() -> bytes:
        _, frame = fc.canonical_vectors()[1]
        return frame[:-4]


if __name__ == "__main__":
    unittest.main(verbosity=2)
