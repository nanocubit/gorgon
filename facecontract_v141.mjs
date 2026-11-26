// facecontract_v141.mjs — ESM JS side of the facecontract v1.4.1 lock.
// Byte-for-byte mirror of facecontract_v141.py (same 32-byte header layout,
// same IEEE CRC-32 over the body). verify.js stays CommonJS; this module is
// ESM (.mjs) so client-side tools can import it without package.json changes.
//
// Layout (little-endian):
//   0  magic "GRG1" | 4 ver_major | 5 ver_minor | 6 flags | 7 bs_count
//   8  fps u16 | 10 chunk_size u16 | 12 seq u32 | 16 dur_us u32
//   20 payload_len u32 | 24 t_ns u64 | 32 payload | 32+N crc32 u32

import { Buffer } from "node:buffer";

export const MAGIC = Buffer.from("GRG1", "ascii");
export const VERSION_MAJOR = 1;
export const VERSION_MINOR = 4;
export const FLAG_IDLE = 0x01;
export const FLAG_SPEAKING = 0x02;
export const KNOWN_FLAGS = FLAG_IDLE | FLAG_SPEAKING;
export const HEADER_SIZE = 32;
export const FRAME_OVERHEAD = 36;
// crc32(M || LE-crc32(M)) is constant for IEEE CRC-32 — the residue check.
export const CRC_RESIDUE = 0x2144DF1C;

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) !== 0 ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(buf) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) {
    c = CRC_TABLE[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  }
  return (c ^ 0xFFFFFFFF) >>> 0;
}

export class ContractError extends Error {}

export function decodeFrame(buf) {
  if (buf.length < FRAME_OVERHEAD) {
    throw new ContractError(`frame too short: ${buf.length}`);
  }
  if (!buf.subarray(0, 4).equals(MAGIC)) throw new ContractError("bad magic");
  const vmaj = buf.readUInt8(4);
  const vmin = buf.readUInt8(5);
  if (vmaj !== VERSION_MAJOR || vmin !== VERSION_MINOR) {
    throw new ContractError(`unsupported version: ${vmaj}.${vmin}`);
  }
  const flags = buf.readUInt8(6);
  const bsCount = buf.readUInt8(7);
  const fps = buf.readUInt16LE(8);
  const chunkSize = buf.readUInt16LE(10);
  const seq = buf.readUInt32LE(12);
  const durUs = buf.readUInt32LE(16);
  const payloadLen = buf.readUInt32LE(20);
  const tNs = Number(buf.readBigUInt64LE(24));
  if (fps < 1) throw new ContractError(`fps must be >= 1: ${fps}`);
  if (chunkSize < 1) throw new ContractError(`chunk_size must be >= 1: ${chunkSize}`);
  if (payloadLen !== buf.length - FRAME_OVERHEAD) {
    throw new ContractError(
      `payload_len mismatch: header=${payloadLen} actual=${buf.length - FRAME_OVERHEAD}`);
  }
  if (payloadLen % 4 !== 0) {
    throw new ContractError(`payload_len not a multiple of 4: ${payloadLen}`);
  }
  const storedCrc = buf.readUInt32LE(HEADER_SIZE + payloadLen);
  const computedCrc = crc32(buf.subarray(0, HEADER_SIZE + payloadLen));
  if (storedCrc !== computedCrc) {
    throw new ContractError(
      `CRC mismatch: stored=0x${storedCrc.toString(16)} computed=0x${computedCrc.toString(16)}`);
  }
  const ones =
    ((flags & FLAG_IDLE) !== 0 ? 1 : 0) + ((flags & FLAG_SPEAKING) !== 0 ? 1 : 0);
  if (ones !== 1) throw new ContractError("I3 violation: flags must be one-hot");
  const nFloats = payloadLen / 4;
  if (!Number.isInteger(nFloats / chunkSize)) {
    throw new ContractError("payload not a multiple of chunk_size");
  }
  if (nFloats / chunkSize !== bsCount) {
    throw new ContractError(
      `bs_count mismatch: header=${bsCount} derived=${nFloats / chunkSize}`);
  }
  if (tNs > Number.MAX_SAFE_INTEGER) {
    throw new ContractError("t_ns above 2^53: JSON compare unsafe");
  }
  const payload = new Array(nFloats);
  for (let i = 0; i < nFloats; i++) {
    payload[i] = buf.readFloatLE(HEADER_SIZE + i * 4);
  }
  return {
    seq, t_ns: tNs, dur_us: durUs, fps, chunk_size: chunkSize,
    bs_count: bsCount, flags, payload,
  };
}

export function encodeFrame(f) {
  const n = f.payload.length;
  if (n % f.chunk_size !== 0) {
    throw new ContractError("payload not a multiple of chunk_size");
  }
  const buf = Buffer.alloc(FRAME_OVERHEAD + n * 4);
  MAGIC.copy(buf, 0);
  buf.writeUInt8(VERSION_MAJOR, 4);
  buf.writeUInt8(VERSION_MINOR, 5);
  buf.writeUInt8(f.flags, 6);
  buf.writeUInt8(Math.floor(n / f.chunk_size), 7);
  buf.writeUInt16LE(f.fps, 8);
  buf.writeUInt16LE(f.chunk_size, 10);
  buf.writeUInt32LE(f.seq, 12);
  buf.writeUInt32LE(f.dur_us, 16);
  buf.writeUInt32LE(n * 4, 20);
  buf.writeBigUInt64LE(BigInt(f.t_ns), 24);
  for (let i = 0; i < n; i++) buf.writeFloatLE(f.payload[i], HEADER_SIZE + i * 4);
  buf.writeUInt32LE(crc32(buf.subarray(0, HEADER_SIZE + n * 4)), HEADER_SIZE + n * 4);
  return buf;
}

export function flagName(flags) {
  if (flags === FLAG_IDLE) return "IDLE";
  if (flags === FLAG_SPEAKING) return "SPEAKING";
  return `0x${flags.toString(16)}`;
}

// Walk a concatenated .fc14 stream (raw frames, no container).
export function unpackStream(buf) {
  const out = [];
  let off = 0;
  while (off < buf.length) {
    if (buf.length - off < FRAME_OVERHEAD) {
      throw new ContractError(`trailing bytes at ${off}: not a full header`);
    }
    const payloadLen = buf.readUInt32LE(off + 20);
    const flen = FRAME_OVERHEAD + payloadLen;
    const frame = buf.subarray(off, off + flen);
    out.push({ bytes: frame, decoded: decodeFrame(frame) });
    off += flen;
  }
  return out;
}

// CrossfadeFilter — reference implementation of PROTOCOL §7.2 (MUST).
//
// Called per-frame (not per-chunk) by the client render loop. The client
// unpacks each frame's bsValues from the chunk, passes them here, and gets
// back either the same array (no crossfade) or a new blended array.
//
// State machine:
//   IDLE ─(isSpeaking=true)─► TRACKING (keeps the last rendered pose in _last)
//   TRACKING ─(isSpeaking=false)─► BLENDING (_startMs=nowMs, _from=_last)
//   BLENDING ─(progress>=1)─► IDLE
//   BLENDING ─(isSpeaking=true)─► TRACKING (abort, resume speech)
//
// _from is the LAST SPEAKING pose, not the first idle frame. Capturing the
// first idle frame instead would render it verbatim at progress=0 — the raw
// speech→idle snap would pass through untouched and the blend would blend
// idle into idle. verify_behavior.mjs Test 7 pins exactly this: without the
// last-speech capture the transition delta stays at 0.23 instead of ~0.04.
//
// Pipeline order (§7.2): unpack → crossfade.processFrame → OEF (§7.1) → rig.
export class CrossfadeFilter {
  constructor(bsCount, crossfadeMs = 200) {
    this.bsCount = bsCount;
    this.crossfadeMs = crossfadeMs;
    this._wasSpeaking = false;
    this._startMs = null;
    this._from = null;
    this._last = null;      // last rendered pose — captured at the transition
    this.active = false;
  }

  /**
   * @param {Float32Array} bsValues - unpacked per-frame blendshapes, length bsCount
   * @param {boolean} isSpeaking    - (chunk.flags & FLAG_SPEAKING) !== 0
   * @param {number} nowMs          - client monotonic time
   * @returns {Float32Array}        - same array if no crossfade, new if blending
   */
  processFrame(bsValues, isSpeaking, nowMs) {
    // Speech resumed mid-crossfade: abort, no smoothing into idle.
    if (this._startMs !== null && isSpeaking) {
      this._startMs = null;
      this._from = null;
      this._wasSpeaking = true;
      this.active = false;
      this._last = Float32Array.from(bsValues);
      return bsValues;
    }

    // Detect SPEAKING -> IDLE at frame boundary. Blend starts from the last
    // SPEECH pose (this._last), so the raw snap is absorbed by the blend.
    if (this._wasSpeaking && !isSpeaking && this._startMs === null) {
      this._startMs = nowMs;
      this._from = this._last
        ? Float32Array.from(this._last)
        : Float32Array.from(bsValues);
    }
    this._wasSpeaking = isSpeaking;

    if (this._startMs === null) {
      this.active = false;
      this._last = Float32Array.from(bsValues);
      return bsValues;
    }

    const progress = Math.min(1, (nowMs - this._startMs) / this.crossfadeMs);
    if (progress >= 1) {
      this._startMs = null;
      this._from = null;
      this.active = false;
      this._last = Float32Array.from(bsValues);
      return bsValues;
    }

    const out = new Float32Array(this.bsCount);
    for (let i = 0; i < this.bsCount; i++) {
      out[i] = this._from[i] * (1 - progress) + bsValues[i] * progress;
    }
    this.active = true;
    this._last = Float32Array.from(out);
    return out;
  }
}
