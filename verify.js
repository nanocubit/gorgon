#!/usr/bin/env node
/*
 * verify.js — independent JavaScript side of the facecontract v1.4.1 lock.
 *
 * Re-implements the wire format from the spec only (no shared code with
 * Python) and checks, for every frame in facecontract_vectors.json:
 *   - decode: magic/version/CRC/one-hot flags/lengths/bs_count consistency;
 *   - every expected field (seq, t_ns, dur_us, fps, chunk_size, bs_count,
 *     flags, payload) against the Python-side fixture;
 *   - re-encode → byte-for-byte equality with the original frame;
 *   - own CRC-32 vs the Python-side CRC;
 *   - CRC-32 residue over the whole frame == 0x2144DF1C.
 * Then over the 16-frame session stream:
 *   - I1: seq strict +1 from 0;  I2/I9: t_ns strict constant step;  I3: flags
 *     one-hot on every frame;
 *   - corruption probe: one flipped payload byte must be rejected by CRC.
 *
 * Usage: node verify.js [facecontract_vectors.json]
 * Exit 0 and "LOCKED. N frames verified." only if everything holds.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const MAGIC = Buffer.from("GRG1", "ascii");
const VERSION_MAJOR = 1;
const VERSION_MINOR = 4;
const FLAG_IDLE = 0x01;
const FLAG_SPEAKING = 0x02;
const HEADER_SIZE = 32;
const FRAME_OVERHEAD = 36;
const CRC_RESIDUE = 0x2144DF1C;

// ── IEEE CRC-32 (zlib poly 0xEDB88320), implemented from scratch ─────────────
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

function crc32(buf) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) {
    c = CRC_TABLE[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  }
  return (c ^ 0xFFFFFFFF) >>> 0;
}

class ContractError extends Error {}

function decodeFrame(buf) {
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

function encodeFrame(f) {
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

function flagName(flags) {
  if (flags === FLAG_IDLE) return "IDLE";
  if (flags === FLAG_SPEAKING) return "SPEAKING";
  return `0x${flags.toString(16)}`;
}

function main() {
  const vecPath = process.argv[2] ||
    path.join(__dirname, "facecontract_vectors.json");
  const doc = JSON.parse(fs.readFileSync(vecPath, "utf8"));
  console.log(`verify.js — independent JS decoder for facecontract v${doc.version}`);

  let failures = 0;
  const decoded = [];

  for (const entry of doc.frames) {
    const buf = Buffer.from(entry.frame_hex, "hex");
    const idx = String(entry.index).padStart(2, " ");
    try {
      const d = decodeFrame(buf);
      const e = entry.expect;
      const problems = [];
      if (d.seq !== e.seq) problems.push(`seq ${d.seq}!=${e.seq}`);
      if (d.t_ns !== e.t_ns) problems.push(`t_ns ${d.t_ns}!=${e.t_ns}`);
      if (d.dur_us !== e.dur_us) problems.push(`dur_us ${d.dur_us}!=${e.dur_us}`);
      if (d.fps !== e.fps) problems.push(`fps ${d.fps}!=${e.fps}`);
      if (d.chunk_size !== e.chunk_size) {
        problems.push(`chunk_size ${d.chunk_size}!=${e.chunk_size}`);
      }
      if (d.bs_count !== e.bs_count) {
        problems.push(`bs_count ${d.bs_count}!=${e.bs_count}`);
      }
      if (d.flags !== e.flags) problems.push(`flags ${d.flags}!=${e.flags}`);
      if (d.payload.length !== e.payload.length) {
        problems.push(`payload len ${d.payload.length}!=${e.payload.length}`);
      } else {
        for (let i = 0; i < d.payload.length; i++) {
          if (Math.abs(d.payload[i] - e.payload[i]) > 1e-6) {
            problems.push(`payload[${i}] ${d.payload[i]}!=${e.payload[i]}`);
            break;
          }
        }
      }
      const rt = encodeFrame(d);
      if (!rt.equals(buf)) problems.push("re-encode bytes differ");
      if (crc32(buf.subarray(0, buf.length - 4)) !== parseInt(entry.crc, 16)) {
        problems.push("python-side CRC mismatch");
      }
      if (crc32(buf) !== CRC_RESIDUE) problems.push("CRC residue mismatch");
      if (problems.length) {
        failures++;
        console.log(`frame ${idx}: FAIL (${problems.join("; ")})`);
      } else {
        console.log(`frame ${idx}: seq=${d.seq} t_ns=${d.t_ns} ` +
          `flags=${flagName(d.flags)} crc=OK roundtrip=OK residue=OK`);
      }
      decoded.push(d);
    } catch (err) {
      failures++;
      console.log(`frame ${idx}: ERROR ${err.message}`);
    }
  }

  // ── session-level invariants over the emitted stream ────────────────────
  // I2 answer (point 4 of the review): the step used to be derived from the
  // stream itself (constant check only). Now it is additionally pinned to the
  // GROUND TRUTH formula computed from the decoded header fields:
  //   dur_ns = floor(chunk_size * 1e9 / fps)   — exact integer step
  //   dur_us = floor(dur_ns / 1000)            — advisory floor, ≤ dur_ns/1000
  // This catches a server that steps t_ns by dur_us*1000 (9 ms/h drift) or a
  // header whose dur_us is not the floor of the real step.
  const step = decoded.length > 1 ? decoded[1].t_ns - decoded[0].t_ns : 0;
  const theoreticalStep = decoded.length > 0
    ? Math.floor((decoded[0].chunk_size * 1e9) / decoded[0].fps) : 0;
  const advisoryOk = decoded.length > 0 &&
    decoded[0].dur_us === Math.floor(theoreticalStep / 1000);
  let seqOk = true;
  let stepOk = true;
  let flagOk = true;
  for (let i = 0; i < decoded.length; i++) {
    if (decoded[i].seq !== i) seqOk = false;
    if (i > 0 && decoded[i].t_ns - decoded[i - 1].t_ns !== step) stepOk = false;
    if (decoded[i].flags !== FLAG_IDLE && decoded[i].flags !== FLAG_SPEAKING) {
      flagOk = false;
    }
  }
  const stepIsTruth = step === theoreticalStep;
  console.log(
    `invariants: I1 seq strict +1 ${seqOk ? "OK" : "FAIL"}; ` +
    `I2/I9 t_ns strict step (+${step}ns) ${stepOk ? "OK" : "FAIL"}, ` +
    `== chunk_size*1e9/fps ${stepIsTruth ? "OK" : `FAIL (expected ${theoreticalStep}ns)`}; ` +
    `dur_us advisory floor ${advisoryOk ? "OK" : "FAIL"}; ` +
    `I3 flags one-hot ${flagOk ? "OK" : "FAIL"}`);

  // ── corruption probe: one flipped payload byte must be rejected by CRC ──────
  let probeOk = false;
  if (doc.frames.length > 7) {
    const bad = Buffer.from(doc.frames[7].frame_hex, "hex");
    bad[HEADER_SIZE + 8] ^= 0xFF;
    try {
      decodeFrame(bad);
    } catch (err) {
      probeOk = true;
    }
  }
  console.log(`corruption probe: crc rejection ${probeOk ? "OK" : "FAIL"}`);

  const expectedCount = 16;
  if (failures === 0 && seqOk && stepOk && stepIsTruth && advisoryOk &&
      flagOk && probeOk && decoded.length === expectedCount) {
    console.log(`LOCKED. ${decoded.length} frames verified.`);
    process.exit(0);
  }
  console.log(`FAILED: frames=${decoded.length}/${expectedCount} ` +
    `failures=${failures} invariants=` +
    `${seqOk && stepOk && stepIsTruth && advisoryOk && flagOk} probe=${probeOk}`);
  process.exit(1);
}

main();
