// verify_behavior.mjs — client-side behavior test for FaceContract v1.4.1.
// Loads test_vectors/idle_breathing_blink.fc14, walks the raw frame stream,
// pushes every channel through the §7.1 One-Euro-Filter profiles and asserts
// the visual thresholds. Measures instead of guessing: if a threshold fails,
// the printed numbers say which knob (fc_min / beta) to move.
//
// Run: node verify_behavior.mjs [path/to/idle_breathing_blink.fc14]
// Exit 0 and "BEHAVIOR OK" only if all thresholds hold.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  decodeFrame, crc32, CRC_RESIDUE, ContractError, unpackStream,
  CrossfadeFilter, FLAG_SPEAKING,
} from "./facecontract_v141.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));

// ─── OEF with per-channel tuning (PROTOCOL §7.1) ────────────────────────

const BLINK_CHANNELS = new Set(["eyeBlinkLeft", "eyeBlinkRight"]);
const ARTICULATION_CHANNELS = new Set([
  "jawOpen", "jawForward", "jawLeft", "jawRight",
  "mouthFunnel", "mouthPucker", "mouthClose",
  "mouthPressLeft", "mouthPressRight",
  "mouthSmileLeft", "mouthSmileRight",
  "mouthFrownLeft", "mouthFrownRight",
  "mouthStretchLeft", "mouthStretchRight",
  "mouthLowerDownLeft", "mouthLowerDownRight",
  "mouthUpperUpLeft", "mouthUpperUpRight",
  "tongueOut",
]);

class OneEuroFilter {
  constructor(name, freq = 30) {
    this.freq = freq;
    if (BLINK_CHANNELS.has(name)) {
      this.minCutoff = 5.0; this.beta = 0.10;
    } else if (ARTICULATION_CHANNELS.has(name)) {
      this.minCutoff = 1.5; this.beta = 0.03;
    } else {
      this.minCutoff = 1.0; this.beta = 0.007;
    }
    this.xPrev = null;
    this.dxPrev = 0;
  }
  filter(x) {
    if (this.xPrev === null) { this.xPrev = x; this.dxPrev = 0; return x; }
    const dx = (x - this.xPrev) * this.freq;
    const ed = 0.5 * (Math.abs(dx) + Math.abs(this.dxPrev));
    const cutoff = this.minCutoff + this.beta * ed;
    const tau = 1.0 / (2 * Math.PI * cutoff);
    const alpha = 1.0 / (1.0 + tau * this.freq);
    const xf = alpha * x + (1 - alpha) * this.xPrev;
    this.xPrev = xf; this.dxPrev = dx;
    return xf;
  }
}

// ─── Channel names (Core 32, order frozen per PROTOCOL §3) ──────────────

const CORE_NAMES = [
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
];
const CORE_IDX = Object.fromEntries(CORE_NAMES.map((n, i) => [n, i]));

// ─── Assertions ─────────────────────────────────────────────────────────────

const failures = [];
function check(cond, msg) {
  console.log(`  ${cond ? "PASS" : "FAIL"}  ${msg}`);
  if (!cond) failures.push(msg);
}

// ─── Tests ──────────────────────────────────────────────────────────────

function runFilterTests(frames) {
  console.log("\n[1] OEF output series - no NaN, all finite, in [0, 1]");
  const filters = CORE_NAMES.map((n) => new OneEuroFilter(n));
  const series = CORE_NAMES.map(() => []);

  for (const { decoded: fr } of frames) {
    const stride = fr.bs_count;
    for (let f = 0; f < fr.chunk_size; f++) {
      for (let c = 0; c < stride; c++) {
        series[c].push(filters[c].filter(fr.payload[f * stride + c]));
      }
    }
  }

  let allFinite = true;
  let allInRange = true;
  for (const col of series) {
    for (const v of col) {
      if (!Number.isFinite(v)) allFinite = false;
      if (v < -0.001 || v > 1.001) allInRange = false;
    }
  }
  check(allFinite, "all filtered values finite (no NaN/Inf)");
  check(allInRange, "all filtered values in [0, 1] +/- tolerance");
  return series;
}

function runBlinkPeak(series) {
  console.log("\n[2] Blink peak (filtered) >= 0.85");
  const blinkL = series[CORE_IDX.eyeBlinkLeft];
  const blinkR = series[CORE_IDX.eyeBlinkRight];
  const peakL = Math.max(...blinkL);
  const peakR = Math.max(...blinkR);
  console.log(`    eyeBlinkLeft  peak = ${peakL.toFixed(4)}`);
  console.log(`    eyeBlinkRight peak = ${peakR.toFixed(4)}`);
  check(peakL >= 0.85, `eyeBlinkLeft peak ${peakL.toFixed(3)} >= 0.85`);
  check(peakR >= 0.85, `eyeBlinkRight peak ${peakR.toFixed(3)} >= 0.85`);
  check(Math.abs(peakL - peakR) < 0.01, "left/right blink symmetric (< 0.01)");
}

function runBreathing(series) {
  console.log("\n[3] Breathing: jawOpen filtered range within [0.005, 0.06]");
  const jaw = series[CORE_IDX.jawOpen];
  const min = Math.min(...jaw);
  const max = Math.max(...jaw);
  const mean = jaw.reduce((a, b) => a + b, 0) / jaw.length;
  console.log(`    jawOpen min=${min.toFixed(4)} max=${max.toFixed(4)} mean=${mean.toFixed(4)}`);
  check(min >= 0.005, `jawOpen min ${min.toFixed(4)} >= 0.005 (filtered floor)`);
  check(max <= 0.06, `jawOpen max ${max.toFixed(4)} <= 0.06 (no run-away)`);
  check(max - min > 0.01, `jawOpen range ${(max - min).toFixed(4)} > 0.01 (visible breathing)`);
}

function runDecay(series) {
  console.log("\n[4] Blink decay: within 300 ms (9 frames), eyeBlinkLeft < 0.05");
  const blinkL = series[CORE_IDX.eyeBlinkLeft];
  const peakIdx = blinkL.indexOf(Math.max(...blinkL));
  const framesToDecay = 9; // 300 ms @ 30 fps

  if (peakIdx + framesToDecay >= blinkL.length) {
    check(false,
      `not enough frames after peak (${blinkL.length - peakIdx - 1} < ${framesToDecay})`);
    return;
  }
  const decayed = blinkL[peakIdx + framesToDecay];
  console.log(`    after ${framesToDecay} frames (300 ms): ${decayed.toFixed(4)}`);
  check(decayed < 0.05, `blink decayed to ${decayed.toFixed(4)} < 0.05 within 300 ms`);
  const tailMin = Math.min(...blinkL.slice(peakIdx + framesToDecay));
  check(tailMin < 0.02, `blink tail min ${tailMin.toFixed(4)} < 0.02 (fully open)`);
}

function runIdleBase(series) {
  console.log("\n[5] Idle base: non-active channels stay near 0 (< 0.06)");
  const active = new Set([
    "eyeBlinkLeft", "eyeBlinkRight", "jawOpen", "mouthClose", "browInnerUp",
  ]);
  let worst = 0;
  let worstName = "";
  for (let c = 0; c < CORE_NAMES.length; c++) {
    const name = CORE_NAMES[c];
    if (active.has(name)) continue;
    const m = Math.max(...series[c]);
    if (m > worst) { worst = m; worstName = name; }
  }
  console.log(`    largest non-active channel: ${worstName} = ${worst.toFixed(4)}`);
  check(worst < 0.06, `non-active max ${worst.toFixed(4)} < 0.06`);
}

function runResidue(frames) {
  console.log("\n[6] CRC residue invariant (structural, per frame)");
  let ok = true;
  let bad = -1;
  for (const { bytes } of frames) {
    if (crc32(bytes) !== CRC_RESIDUE) { ok = false; bad = bytes.length; }
  }
  check(ok, bad >= 0
    ? `residue mismatch in frame of ${bad} bytes`
    : `crc32(frame) == 0x${CRC_RESIDUE.toString(16)} for all ${frames.length} frames`);
}

function runCrossfadeTest() {
  console.log("\n[7] Crossfade: SPEAKING→IDLE delta and idle settling (§7.2)");
  const target = path.join(here, "test_vectors", "speech_to_idle.fc14");
  if (!fs.existsSync(target)) {
    check(false,
      `${path.basename(target)} missing — run python test_vectors/decode_b64.py ` +
      "or python facecontract_v141.py --vectors first");
    return;
  }

  // Same unpacker as the idle vector — bs_count-agnostic.
  const frames = unpackStream(fs.readFileSync(target));
  const bsCount = frames[0].decoded.bs_count;
  check(bsCount === 32, `bs_count is 32 (got ${bsCount})`);

  // Flatten to per-frame samples. NOTE: isSpeaking tests FLAG_SPEAKING (0x02);
  // FLAG_IDLE is the 0x01 bit, so (flags & 0x01) would classify every frame
  // as speaking and the transition would never fire.
  const perFrame = [];
  let nowMs = 0;
  for (const { decoded: fr } of frames) {
    const isSpeaking = (fr.flags & FLAG_SPEAKING) !== 0;
    const stride = fr.bs_count;
    for (let f = 0; f < fr.chunk_size; f++) {
      const v = new Float32Array(stride);
      for (let c = 0; c < stride; c++) v[c] = fr.payload[f * stride + c];
      perFrame.push({ values: v, isSpeaking, nowMs });
      nowMs += 1000 / 30;
    }
  }
  check(perFrame.length === 24, `24 frames loaded (got ${perFrame.length})`);

  // Locate the SPEAKING→IDLE transition.
  let transitionIdx = -1;
  for (let i = 1; i < perFrame.length; i++) {
    if (perFrame[i - 1].isSpeaking && !perFrame[i].isSpeaking) {
      transitionIdx = i;
      break;
    }
  }
  check(transitionIdx === 12, `transition at frame 12 (got ${transitionIdx})`);

  // jawOpen index from the frozen Core-32 order (CORE_IDX), not a hardcoded 10.
  const jawIdx = CORE_IDX.jawOpen;
  const cf = new CrossfadeFilter(bsCount, 200);
  const outJaw = [];
  for (const pf of perFrame) {
    const blended = cf.processFrame(pf.values, pf.isSpeaking, pf.nowMs);
    outJaw.push(blended[jawIdx]);
  }

  // Assert 1: the raw signal actually snaps at the transition (the test is
  // meaningful only if it would fail without the filter).
  const rawDelta = Math.abs(perFrame[transitionIdx].values[jawIdx]
                          - perFrame[transitionIdx - 1].values[jawIdx]);
  console.log(`    raw jawOpen delta at transition: ${rawDelta.toFixed(4)}`);
  check(rawDelta > 0.15, `raw delta ${rawDelta.toFixed(4)} > 0.15 (test is meaningful)`);

  // Assert 2: crossfaded per-frame delta stays small (no visible snap).
  let maxDelta = 0;
  for (let i = transitionIdx; i < Math.min(transitionIdx + 12, outJaw.length); i++) {
    const d = Math.abs(outJaw[i] - outJaw[i - 1]);
    if (d > maxDelta) maxDelta = d;
  }
  console.log(`    max crossfaded jawOpen delta in window: ${maxDelta.toFixed(4)}`);
  check(maxDelta < 0.15, `crossfaded max delta ${maxDelta.toFixed(4)} < 0.15 (no snap)`);

  // Assert 3: settles into the idle breathing range.
  const tail = outJaw.slice(-5);
  const tailMean = tail.reduce((a, b) => a + b, 0) / tail.length;
  console.log(`    jawOpen tail mean: ${tailMean.toFixed(4)}`);
  check(tailMean >= 0.02 && tailMean <= 0.06,
        `tail in idle range [0.02, 0.06]`);

  // Assert 4: crossfade was active for ~200 ms (6 frames @ 30 fps, ±2).
  const cf2 = new CrossfadeFilter(bsCount, 200);
  const activeFrames = [];
  for (const pf of perFrame) {
    cf2.processFrame(pf.values, pf.isSpeaking, pf.nowMs);
    if (cf2.active) activeFrames.push(pf.nowMs);
  }
  check(Math.abs(activeFrames.length - 6) <= 2,
        `crossfade active for ${activeFrames.length} frames (±2 of 6)`);
}

// ─── Main ────────────────────────────────────────────────────────────────

function main() {
  const target = process.argv[2] ||
    path.join(here, "test_vectors", "idle_breathing_blink.fc14");
  if (!fs.existsSync(target)) {
    console.error(`Missing ${target}. Run: python facecontract_v141.py --vectors`);
    process.exit(1);
  }

  console.log(`Behavior test: ${target}`);
  const frames = unpackStream(fs.readFileSync(target));
  const subFrames = frames.reduce((a, f) => a + f.decoded.chunk_size, 0);
  console.log(`  loaded ${frames.length} chunk frame(s), ${subFrames} total sub-frames`);
  if (frames.length === 0) {
    console.error("  no frames decoded");
    process.exit(1);
  }

  const series = runFilterTests(frames);
  runBlinkPeak(series);
  runBreathing(series);
  runDecay(series);
  runIdleBase(series);
  runResidue(frames);
  runCrossfadeTest();

  console.log("");
  if (failures.length === 0) {
    console.log("BEHAVIOR OK — OEF tuning validated on idle_breathing_blink.fc14, " +
      "crossfade gate on speech_to_idle.fc14");
    process.exit(0);
  }
  console.log(`BEHAVIOR FAILED: ${failures.length} failure(s):`);
  for (const f of failures) console.log(`   - ${f}`);
  process.exit(1);
}

main();
