"""
facecontract v1.4.1 — wire format contract for the Gorgon chunk stream.

Frame layout (little-endian, fixed 32-byte header):

  offset  size  field
  ------  ----  -----
  0       4     magic "GRG1"
  4       1     version_major = 1
  5       1     version_minor = 4
  6       1     flags         bit0 FLAG_IDLE, bit1 FLAG_SPEAKING (one-hot, I3)
  7       1     bs_count      blendshape count (I6: in SUPPORTED_BS_COUNTS)
  8       2     fps           u16
  10      2     chunk_size    frames per chunk, u16
  12      4     seq           u32, strict +1 per emitted frame (I1)
  16      4     dur_us        u32, nominal chunk duration (advisory; t_ns is
                              authoritative for pacing)
  20      4     payload_len   u32, payload byte length (N)
  24      8     t_ns          u64, frame start time, strict step (I2/I9)
  32      N     payload       N/4 float32 LE values, N = bs_count*chunk_size*4
  32+N    4     crc32         IEEE CRC-32 (zlib poly 0xEDB88320) over [0, 32+N)

encode() is atomic: it either returns a fully valid frame or raises without
producing bytes. decode() validates structure, CRC and flag semantics.

Self-test (`python facecontract_v141.py`):
  encodes 5 canonical vectors, prints their CRCs — the numbers must be
  identical across runs (determinism gate), then decodes + re-encodes each
  vector and writes facecontract_vectors.json (16-frame session scenario)
  consumed by verify.js (independent JS decoder). It also rebuilds the §8
  conformance streams in memory and prints their whole-blob CRC-32 — the
  constants pinned by the CI "Golden CRC pin" step (workflow contract-lock.yml).
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
import zlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "MAGIC", "VERSION_MAJOR", "VERSION_MINOR",
    "FLAG_IDLE", "FLAG_SPEAKING", "KNOWN_FLAGS", "SUPPORTED_BS_COUNTS",
    "FaceContractError", "encode", "decode",
    "canonical_vectors", "build_session_frames", "dump_vectors",
]

MAGIC = b"GRG1"
VERSION_MAJOR = 1
VERSION_MINOR = 4

FLAG_IDLE = 0x01
FLAG_SPEAKING = 0x02
KNOWN_FLAGS = FLAG_IDLE | FLAG_SPEAKING

SUPPORTED_BS_COUNTS = frozenset({1, 2, 4, 8, 32, 52, 72})
# Production rig levels (PROTOCOL §3): Core=32, Extended=52, Premium=72.
# {1, 2, 4, 8} are test-scale levels kept so the locked conformance vectors
# (bs_count=4) and CI synthetic streams stay valid. Adding levels is
# backward-compatible: it does not change the header layout.

_HEADER = struct.Struct("<4sBBBBHHIIIQ")     # 32 bytes, no padding ("<")
_HEADER_SIZE = _HEADER.size                  # 32
_CRC = struct.Struct("<I")
_FRAME_OVERHEAD = _HEADER_SIZE + _CRC.size   # 36


class FaceContractError(ValueError):
    """Raised on any wire-format violation (bad magic/version/CRC/flags)."""


# CRC-32 residue: crc32(frame WITH its trailing CRC field) is a constant for
# any correctly framed message — a free structural integrity check.
CRC_RESIDUE = 0x2144DF1C


def frame_crc(frame: bytes) -> int:
    """CRC-32 over the frame body (all bytes before the trailing CRC field) —
    the value stored in the frame's last 4 bytes."""
    return zlib.crc32(bytes(frame)[:-_CRC.size]) & 0xFFFFFFFF


def _check_float(v: float, idx: int) -> float:
    v = float(v)
    if v != v or v == float("inf") or v == float("-inf"):
        raise FaceContractError(f"payload[{idx}] is not finite: {v!r}")
    return v


def encode(
    *,
    seq: int,
    t_ns: int,
    dur_us: int,
    fps: int,
    chunk_size: int,
    payload: Sequence[float],
    flags: int,
    head_pose: Optional[Any] = None,
) -> bytes:
    """Encode one chunk frame. Atomic: raises before touching any state."""
    if not isinstance(seq, int) or isinstance(seq, bool) or not (0 <= seq <= 0xFFFFFFFF):
        raise FaceContractError(f"seq out of u32 range: {seq!r}")
    if not isinstance(t_ns, int) or isinstance(t_ns, bool) or not (0 <= t_ns < 2 ** 64):
        raise FaceContractError(f"t_ns out of u64 range: {t_ns!r}")
    if not isinstance(dur_us, int) or not (0 <= dur_us <= 0xFFFFFFFF):
        raise FaceContractError(f"dur_us out of u32 range: {dur_us!r}")
    if not isinstance(fps, int) or not (1 <= fps <= 0xFFFF):
        raise FaceContractError(f"fps out of u16 range: {fps!r}")
    if not isinstance(chunk_size, int) or chunk_size < 1:
        raise FaceContractError(f"chunk_size must be >= 1: {chunk_size!r}")
    if flags & ~KNOWN_FLAGS:
        raise FaceContractError(f"unknown flag bits: 0x{flags:08X}")
    if flags not in (FLAG_IDLE, FLAG_SPEAKING):
        raise FaceContractError(
            "I3 violation: exactly one of FLAG_IDLE/FLAG_SPEAKING must be set")
    if head_pose is not None:
        raise FaceContractError("head_pose is reserved in v0 wire format; pass None")

    values = list(payload)
    n = len(values)
    if n == 0:
        raise FaceContractError("payload must not be empty")
    if n % chunk_size != 0:
        raise FaceContractError(
            f"payload length {n} is not a multiple of chunk_size {chunk_size}")
    bs_count = n // chunk_size
    if bs_count not in SUPPORTED_BS_COUNTS:
        raise FaceContractError(
            f"I6 violation: bs_count {bs_count} not in {sorted(SUPPORTED_BS_COUNTS)}")

    blob = struct.pack(f"<{n}f", *[_check_float(v, i) for i, v in enumerate(values)])
    header = _HEADER.pack(
        MAGIC, VERSION_MAJOR, VERSION_MINOR, flags, bs_count,
        fps, chunk_size, seq, dur_us, len(blob), t_ns,
    )
    body = header + blob
    return body + _CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)


def decode(frame: bytes) -> Dict[str, Any]:
    """Decode and fully validate one frame. Raises FaceContractError on any
    violation; returned payload floats are the f32 values widened to f64."""
    buf = bytes(frame)
    if len(buf) < _FRAME_OVERHEAD:
        raise FaceContractError(f"frame too short: {len(buf)} bytes")
    (magic, vmaj, vmin, flags, bs_count, fps, chunk_size,
     seq, dur_us, payload_len, t_ns) = _HEADER.unpack_from(buf, 0)
    if magic != MAGIC:
        raise FaceContractError(f"bad magic: {magic!r}")
    if (vmaj, vmin) != (VERSION_MAJOR, VERSION_MINOR):
        raise FaceContractError(f"unsupported version: {vmaj}.{vmin}")
    if fps < 1:
        raise FaceContractError(f"fps must be >= 1: {fps}")
    if chunk_size < 1:
        raise FaceContractError(f"chunk_size must be >= 1: {chunk_size}")
    if payload_len != len(buf) - _FRAME_OVERHEAD:
        raise FaceContractError(
            f"payload_len mismatch: header={payload_len} "
            f"actual={len(buf) - _FRAME_OVERHEAD}")
    if payload_len % 4 != 0:
        raise FaceContractError(f"payload_len is not a multiple of 4: {payload_len}")
    (stored_crc,) = _CRC.unpack_from(buf, _HEADER_SIZE + payload_len)
    computed_crc = zlib.crc32(buf[:_HEADER_SIZE + payload_len]) & 0xFFFFFFFF
    if stored_crc != computed_crc:
        raise FaceContractError(
            f"CRC mismatch: stored=0x{stored_crc:08X} computed=0x{computed_crc:08X}")
    if flags & ~KNOWN_FLAGS:
        raise FaceContractError(f"unknown flag bits: 0x{flags:08X}")
    if flags not in (FLAG_IDLE, FLAG_SPEAKING):
        raise FaceContractError("I3 violation: flags must be one-hot (IDLE or SPEAKING)")
    n_floats = payload_len // 4
    if n_floats % chunk_size != 0:
        raise FaceContractError(
            f"payload {n_floats} floats is not a multiple of chunk_size {chunk_size}")
    if n_floats // chunk_size != bs_count:
        raise FaceContractError(
            f"bs_count mismatch: header={bs_count} derived={n_floats // chunk_size}")
    payload = list(struct.unpack(
        f"<{n_floats}f", buf[_HEADER_SIZE:_HEADER_SIZE + payload_len]))
    return {
        "seq": seq,
        "t_ns": t_ns,
        "dur_us": dur_us,
        "fps": fps,
        "chunk_size": chunk_size,
        "bs_count": bs_count,
        "flags": flags,
        "payload": payload,
        "head_pose": None,  # v0: reserved
    }


# ── deterministic test vectors ────────────────────────────────────────────────

# Core-32 channel order — FROZEN (PROTOCOL §3). Index map used by the
# idle_breathing_blink vector: eyeBlinkLeft=0, eyeBlinkRight=1,
# browInnerUp=6, jawOpen=10, mouthClose=23.
CORE32_CHANNEL_NAMES = [
    "eyeBlinkLeft", "eyeBlinkRight", "eyeWideLeft", "eyeWideRight",
    "browDownLeft", "browDownRight", "browInnerUp",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthFunnel", "mouthPucker",
    "mouthSmileLeft", "mouthSmileRight",
    "mouthFrownLeft", "mouthFrownRight",
    "mouthStretchLeft", "mouthStretchRight",
    "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "mouthClose", "mouthPressLeft", "mouthPressRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight", "tongueOut",
]
assert len(CORE32_CHANNEL_NAMES) == 32

# Frozen Core-32 name -> index map (§3). Vector generators address channels by
# NAME, never by raw index — a reorder must break loudly here, not silently.
NAME_TO_INDEX = {name: idx for idx, name in enumerate(CORE32_CHANNEL_NAMES)}


def _det_payload(n: int, seed: int) -> List[float]:
    x = seed & 0x7FFFFFFF
    out: List[float] = []
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((x % 10001) / 10000.0)
    return out


def canonical_vectors() -> List[Tuple[str, bytes]]:
    """The 5 canonical vectors behind the '5 CRC' determinism gate."""
    return [
        ("minimal_idle", encode(
            seq=0, t_ns=0, dur_us=33333, fps=30, chunk_size=1,
            payload=[0.0], flags=FLAG_IDLE)),
        ("speech_bs4", encode(
            seq=1, t_ns=133333333, dur_us=133333, fps=30, chunk_size=4,
            payload=_det_payload(16, 1), flags=FLAG_SPEAKING)),
        ("hangover_tail", encode(
            seq=42, t_ns=5600000000, dur_us=133333, fps=30, chunk_size=4,
            payload=_det_payload(16, 2), flags=FLAG_SPEAKING)),
        ("extreme_values", encode(
            seq=0xFFFFFFFF, t_ns=4123456789123, dur_us=0xFFFFFFFF, fps=30,
            chunk_size=4, payload=_det_payload(16, 3), flags=FLAG_IDLE)),
        ("bs8_chunk1", encode(
            seq=7, t_ns=233333333, dur_us=33333, fps=30, chunk_size=1,
            payload=_det_payload(8, 4), flags=FLAG_SPEAKING)),
    ]


def build_session_frames() -> List[bytes]:
    """16-frame session scenario for verify.js:
    3 idle → 6 speech (fresh model chunks P1..P6) → 3 speech (hangover,
    hold-last tail of P6) → 4 idle. seq strict +1, t_ns strict step."""
    fps, chunk = 30, 4
    dur_us = chunk * 1_000_000 // fps          # 133333
    dur_ns = chunk * 1_000_000_000 // fps      # 133333333
    idle_payload = [0.03, 0.02, 0.05, 0.01] * 4   # bs_count = 4 → 16 floats
    p = {k: _det_payload(16, k) for k in range(1, 7)}
    spec: List[Tuple[int, Optional[List[float]]]] = (
        [(FLAG_IDLE, None)] * 3
        + [(FLAG_SPEAKING, p[k]) for k in range(1, 7)]
        + [(FLAG_SPEAKING, p[6])] * 3
        + [(FLAG_IDLE, None)] * 4
    )
    return [
        encode(seq=i, t_ns=i * dur_ns, dur_us=dur_us, fps=fps, chunk_size=chunk,
               payload=payload if payload is not None else idle_payload,
               flags=flags)
        for i, (flags, payload) in enumerate(spec)
    ]


def dump_vectors(path: str) -> None:
    """Write the cross-language verification fixture for verify.js."""
    frames = build_session_frames()
    doc: Dict[str, Any] = {
        "magic": MAGIC.decode("ascii"),
        "version": f"{VERSION_MAJOR}.{VERSION_MINOR}",
        "endianness": "little",
        "crc": "ieee-crc32 (zlib poly 0xEDB88320)",
        "header_size": _HEADER_SIZE,
        "frame_overhead": _FRAME_OVERHEAD,
        "canonical_crcs": {
            name: f"{frame_crc(frame):08x}"
            for name, frame in canonical_vectors()
        },
        "crc_residue": f"{CRC_RESIDUE:08x}",
        "frames": [],
    }
    for i, frame in enumerate(frames):
        d = decode(frame)
        doc["frames"].append({
            "index": i,
            "frame_hex": frame.hex(),
            "crc": f"{frame_crc(frame):08x}",
            "expect": {
                "seq": d["seq"], "t_ns": d["t_ns"], "dur_us": d["dur_us"],
                "fps": d["fps"], "chunk_size": d["chunk_size"],
                "bs_count": d["bs_count"], "flags": d["flags"],
                "payload": d["payload"],
            },
        })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


# ── §8 conformance vectors (.fc14 raw frame streams) ──────────────────────────

def build_idle_blink_frames(chunks: int = 8, fps: int = 30,
                            chunk_size: int = 4) -> List[bytes]:
    """idle_breathing_blink.fc14 source: Core-32 idle stream with breathing on
    jawOpen and one blink on eyeBlinkLeft/Right. Raw signal is designed so the
    §7.1 OEF profiles must reproduce: blink peak >= 0.85, jawOpen filtered
    inside [0.005, 0.06] with visible range, blink decay < 0.05 in 300 ms."""
    n = chunks * chunk_size  # 32 sub-frames
    blink_raw = {13: 0.4, 14: 0.9, 15: 1.0, 16: 1.0,
                 17: 0.5, 18: 0.15, 19: 0.05, 20: 0.02}
    rows: List[List[float]] = []
    for i in range(n):
        row = [0.001] * 32
        blink = blink_raw.get(i, 0.001)
        row[0] = blink                                            # eyeBlinkLeft
        row[1] = blink                                            # eyeBlinkRight
        row[6] = 0.005 + 0.002 * math.sin(2 * math.pi * i / 16)   # browInnerUp
        row[10] = 0.032 + 0.026 * math.sin(                       # jawOpen
            2 * math.pi * i / 32 - math.pi / 2)
        row[23] = 0.010 + 0.004 * math.sin(2 * math.pi * i / 32)  # mouthClose
        rows.append(row)
    frames: List[bytes] = []
    for c in range(chunks):
        payload = [v for f in range(chunk_size)
                   for v in rows[c * chunk_size + f]]  # row-major [frame][bs]
        frames.append(encode(
            seq=c, t_ns=c * (chunk_size * 1_000_000_000 // fps),
            dur_us=(chunk_size * 1_000_000_000 // fps) // 1000,
            fps=fps, chunk_size=chunk_size, payload=payload, flags=FLAG_IDLE))
    return frames


def build_speech_to_idle_frames(chunks: int = 6, fps: int = 30,
                                chunk_size: int = 4) -> List[bytes]:
    """speech_to_idle.fc14 source: Core-32 SPEAKING→IDLE transition stream for
    the §7.2 client-side crossfade MUST. Chunks 0-2 FLAG_SPEAKING (jawOpen
    articulation 0.20→0.35→0.26), chunks 3-5 FLAG_IDLE (jawOpen breathing
    ~0.03). Sub-frame 11→12 raw jawOpen delta = 0.23 — a visible snap that
    CrossfadeFilter must compress below 0.15 per rendered frame over the
    200 ms blend (verify_behavior.mjs Test 7)."""
    n = chunks * chunk_size  # 24 sub-frames
    rows: List[List[float]] = []
    for i in range(n):
        row = [0.0] * 32
        if i < 8:
            # chunks 0-1: strong articulation
            row[NAME_TO_INDEX["jawOpen"]] = 0.20 + 0.10 * math.sin(2 * math.pi * i / 4)
            row[NAME_TO_INDEX["mouthUpperUpLeft"]] = 0.15
            row[NAME_TO_INDEX["mouthUpperUpRight"]] = 0.15
            row[NAME_TO_INDEX["mouthLowerDownLeft"]] = 0.10
            row[NAME_TO_INDEX["mouthLowerDownRight"]] = 0.10
        elif i < 12:
            # chunk 2: articulation tail (0.35 → 0.26)
            row[NAME_TO_INDEX["jawOpen"]] = 0.35 - 0.03 * (i - 8)
            row[NAME_TO_INDEX["mouthUpperUpLeft"]] = 0.10
            row[NAME_TO_INDEX["mouthUpperUpRight"]] = 0.10
        else:
            # chunks 3-5: idle breathing
            phase = 2 * math.pi * (i - 12) / 9
            row[NAME_TO_INDEX["jawOpen"]] = 0.03 + 0.01 * math.sin(phase)
            row[NAME_TO_INDEX["mouthClose"]] = 0.85 - 0.02 * math.sin(phase)
        rows.append(row)
    frames: List[bytes] = []
    for c in range(chunks):
        payload = [v for f in range(chunk_size)
                   for v in rows[c * chunk_size + f]]  # row-major [frame][bs]
        flags = FLAG_SPEAKING if c < 3 else FLAG_IDLE
        frames.append(encode(
            seq=c, t_ns=7_000_000_000 + c * (chunk_size * 1_000_000_000 // fps),
            dur_us=(chunk_size * 1_000_000_000 // fps) // 1000,
            fps=fps, chunk_size=chunk_size, payload=payload, flags=flags))
    return frames


def build_fc14_streams() -> List[Tuple[str, bytes]]:
    """Build all §8 conformance streams in memory — the single source of truth
    for both the --vectors writer and the self-test CRC printout (the CI
    "Golden CRC pin" step locks these whole-blob CRC-32 constants).

    head_pose_premium.fc14 is NOT built: FLAG_HEAD_POSE (bit 2) is reserved
    for v1.5; v1.4.1 encode() rejects head_pose by design.
    """
    dur_us = 133333          # 4 frames @ 30 fps, advisory floor
    dur_ns = 133333333

    # silence.fc14 — minimal valid idle frame, Core-32
    silence = encode(seq=0, t_ns=0, dur_us=dur_us, fps=30, chunk_size=4,
                     payload=[0.001] * 128, flags=FLAG_IDLE)  # bs_count=32

    # corrupted_crc.fc14 — silence with one flipped payload byte (negative)
    bad = bytearray(silence)
    bad[40] ^= 0xFF  # inside the payload

    return [
        ("silence.fc14", silence),
        # 3-frame utterance, Extended-52
        ("speech_ru_01.fc14", b"".join(
            encode(seq=i, t_ns=i * dur_ns, dur_us=dur_us, fps=30, chunk_size=4,
                   payload=_det_payload(208, i + 11), flags=FLAG_SPEAKING)
            for i in range(3))),
        # seq 0, 1, 3 (simulated loss), Extended-52. Wire level: still valid
        # frames; gap handling is a CLIENT policy (§6.2).
        ("gap_sequence.fc14", b"".join(
            encode(seq=s, t_ns=s * dur_ns, dur_us=dur_us, fps=30, chunk_size=4,
                   payload=_det_payload(208, s + 21), flags=FLAG_SPEAKING)
            for s in (0, 1, 3))),
        # 8 chunks Core-32, breathing + blink
        ("idle_breathing_blink.fc14", b"".join(build_idle_blink_frames())),
        # 6 chunks Core-32, SPEAKING→IDLE transition for the §7.2 crossfade
        ("speech_to_idle.fc14", b"".join(build_speech_to_idle_frames())),
        # negative: must be rejected by decode()
        ("corrupted_crc.fc14", bytes(bad)),
    ]


def write_fc14_vectors(outdir: str) -> List[Tuple[str, str, int]]:
    """Write the PROTOCOL §8 conformance vectors as raw .fc14 streams."""
    os.makedirs(outdir, exist_ok=True)
    written: List[Tuple[str, str, int]] = []
    for name, blob in build_fc14_streams():
        path = os.path.join(outdir, name)
        with open(path, "wb") as fh:
            fh.write(blob)
        written.append((name, path, len(blob)))
    return written


def _self_test() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    vectors_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        here, "facecontract_vectors.json")
    print("facecontract v1.4.1 — canonical self-test (determinism gate)")
    all_ok = True
    for name, frame in canonical_vectors():
        d = decode(frame)
        re_encoded = encode(
            seq=d["seq"], t_ns=d["t_ns"], dur_us=d["dur_us"], fps=d["fps"],
            chunk_size=d["chunk_size"], payload=d["payload"], flags=d["flags"])
        ok = re_encoded == frame
        all_ok = all_ok and ok
        residue_ok = (zlib.crc32(frame) & 0xFFFFFFFF) == CRC_RESIDUE
        all_ok = all_ok and residue_ok
        crc = frame_crc(frame)
        print(f"  {name:<15} crc=0x{crc:08X}  decode=OK  "
              f"re-encode={'OK' if ok else 'FAIL'}  residue={'OK' if residue_ok else 'FAIL'}")
    # §8 conformance streams: rebuild in memory, validate structurally, and
    # print the whole-blob CRC-32 of each stream. The CI "Golden CRC pin" step
    # greps these lines against its pinned constants — any encoder drift
    # changes them even if the .b64 vectors are regenerated to match.
    n_streams = 0
    for name, blob in build_fc14_streams():
        n_streams += 1
        crc = zlib.crc32(blob) & 0xFFFFFFFF
        n_frames = 0
        off = 0
        ok = True
        note = ""
        while off < len(blob):
            (payload_len,) = struct.unpack_from("<I", blob, off + 20)
            frame = blob[off:off + _FRAME_OVERHEAD + payload_len]
            n_frames += 1
            try:
                if name == "corrupted_crc.fc14":
                    decode(frame)          # negative vector: MUST be rejected
                    ok, note = False, "decode accepted a corrupt frame"
                else:
                    decode(frame)
            except FaceContractError as exc:
                if name != "corrupted_crc.fc14":
                    ok, note = False, str(exc)
            off += _FRAME_OVERHEAD + payload_len
        if off != len(blob):
            ok, note = False, "trailing bytes"
        all_ok = all_ok and ok
        print(f"  {name:<28} crc=0x{crc:08X}  frames={n_frames}  "
              f"bytes={len(blob)}  {'OK' if ok else 'FAIL (' + note + ')'}")

    dump_vectors(vectors_path)
    print(f"  vectors: {os.path.basename(vectors_path)} "
          f"({len(build_session_frames())} frames, session scenario)")
    print(f"5/5 canonical vectors + {n_streams}/{n_streams} conformance streams OK"
          if all_ok else "SELF-TEST FAILED")
    return 0 if all_ok else 1


def _write_vectors_cli(outdir: str) -> int:
    written = write_fc14_vectors(outdir)
    print("facecontract v1.4.1 — §8 conformance vectors")
    for name, path, size in written:
        print(f"  {name:<28} {size:>6} bytes  -> {path}")
    print("  head_pose_premium.fc14    skipped (FLAG_HEAD_POSE reserved, v1.5)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--vectors":
        outdir = sys.argv[2] if len(sys.argv) >= 3 else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "test_vectors")
        raise SystemExit(_write_vectors_cli(outdir))
    raise SystemExit(_self_test())
