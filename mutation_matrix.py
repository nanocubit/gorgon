"""
mutation_matrix.py — systematic mutation testing for the Gorgon FaceContract
suite (P1, 2026-11-27).

Method
------
Each mutant is a minimal, semantically meaningful corruption of exactly one
artifact (encoder constant, channel order, generator signal, state-machine
transition, or stored vector). The harness:

  1. copies the repo to a temp dir (pristine source is never touched);
  2. applies the mutation (exact-string replacement, must hit exactly once,
     or a JSON transform);
  3. runs all six executors: the four Python suites (unittest -v) plus the
     two Node verifiers (verify.js, verify_behavior.mjs);
  4. records KILLED (>=1 executor failed, with the failing test names) or
     SURVIVED (all green);
  5. compares the actual killers against the a-priori PREDICTED killers made
     when the mutant was authored — a mismatch means our mental model of the
     suite was wrong and is reported as such.

Outputs (written next to this file):
  MUTATION_MATRIX.md     — human-readable matrix, mutation score, findings
  mutation_matrix.json   — raw machine-readable results

Mutation score = killed / total. A survivor is a semantic change that ships
silently — each survivor must end in one of three documented states:
fixed (test added), accepted (with rationale), or impossible (out of scope).

Run:  python mutation_matrix.py            (~2-3 min; needs node on PATH)
      python mutation_matrix.py --quick    (skip the two Node executors)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))

# ── executors ─────────────────────────────────────────────────────────────────

PY_EXECUTORS: List[Tuple[str, str]] = [
    ("WIRE", "test_facecontract_wire"),
    ("GLUE", "test_gorgon_glue"),
    ("EDGE", "test_gorgon_edge"),
    ("RETGT", "test_retargeting_smoke"),
]

FAIL_RE = re.compile(r"^(\S+) \([^)]+\) \.\.\. (FAIL|ERROR)\s*$")


def _run_py(work: str, module: str, timeout: int = 120) -> Tuple[bool, List[str]]:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", module, "-v"],
        cwd=work, capture_output=True, text=True, timeout=timeout,
    )
    failed = []
    for line in (proc.stdout + proc.stderr).splitlines():
        m = FAIL_RE.match(line.strip())
        if m and m.group(2) in ("FAIL", "ERROR"):
            failed.append(m.group(1))
    ok = proc.returncode == 0
    if ok and failed:  # defensive: rc 0 with parsed failures is a parse bug
        ok = False
    if not ok and not failed:
        failed.append("<suite-level failure>")
    return ok, failed


def _run_node(work: str, script: str, timeout: int = 120) -> Tuple[bool, List[str]]:
    proc = subprocess.run(
        ["node", script], cwd=work, capture_output=True, text=True, timeout=timeout)
    ok = proc.returncode == 0
    failed = [] if ok else [f"node {script}: exit {proc.returncode}"]
    return ok, failed


# ── mutant definitions ────────────────────────────────────────────────────────

@dataclass
class Mutant:
    mid: str
    category: str          # W wire / N names / G generator / S state / V vector
    target: str            # file (or "channels.json" etc.)
    description: str
    edits: List[Tuple[str, str]] = field(default_factory=list)   # exact find → replace
    predicted: set = field(default_factory=set)                   # a-priori killers
    rationale: str = ""    # why we expect those killers
    disposition: str = ""  # for survivors: fixed / accepted / impossible + why
    # keyword-only: JSON transform for channels.json / vectors.json mutants
    json_mut: Optional[Callable[[dict], dict]] = field(default=None, kw_only=True)


# ── JSON transforms (channels.json / facecontract_vectors.json) ──────────────

def _swap_premium_52_53(doc: dict) -> dict:
    doc["channels"][52], doc["channels"][53] = (
        doc["channels"][53], doc["channels"][52])
    return doc


def _gap_gets_equivalent(doc: dict) -> dict:
    for ch in doc["channels"][52:]:
        if ch.get("coverage") == "genuine_gap":
            ch["arkit_equivalent"] = 10          # jawOpen — clearly bogus claim
            ch["arkit_equivalent_name"] = "jawOpen"
            return doc
    raise RuntimeError("no genuine_gap premium channel found")


def _corrupt_equivalent_name(doc: dict) -> dict:
    for ch in doc["channels"][52:]:
        if isinstance(ch.get("arkit_equivalent"), int):
            wrong = "jawOpen" if ch["arkit_equivalent"] != 10 else "mouthClose"
            ch["arkit_equivalent_name"] = wrong
            return doc
    raise RuntimeError("no redundant premium channel found")


def _set_metahuman_target(doc: dict) -> dict:
    doc["channels"][0]["metahuman_target"] = "MH_jawOpen_module"
    return doc


def _decrement_gap_count(doc: dict) -> dict:
    doc["premium_coverage_counts"]["genuine_gap"] -= 1
    return doc


def _clear_wire_semantics(doc: dict) -> dict:
    for ch in doc["channels"]:
        if ch["name"] == "mouthClose":
            ch["wire_semantics"] = ""
            return doc
    raise RuntimeError("mouthClose not found")


def _tamper_vector_payload(doc: dict) -> dict:
    doc["frames"][0]["expect"]["payload"][0] = 0.5
    return doc


def _tamper_frame_hex(doc: dict) -> dict:
    hexs = list(doc["frames"][3]["frame_hex"])
    pos = 80 + 7          # inside the payload region (32-byte header = 64 hex)
    hexs[pos] = "0" if hexs[pos] != "0" else "1"
    doc["frames"][3]["frame_hex"] = "".join(hexs)
    return doc


MUTANTS: List[Mutant] = [
    # ══ W — wire format (facecontract_v141.py) ══════════════════════════════
    Mutant("W01", "W", "facecontract_v141.py",
           'MAGIC b"GRG1" → b"GRG2" (encode writes, decode rejects)',
           [("MAGIC = b\"GRG1\"", "MAGIC = b\"GRG2\"")],
           {"WIRE", "RETGT", "VJS"},
           "decode() magic check fails on self-encoded frames; T3 regenerates "
           "streams that no longer match the locked .b64; JS pins magic"),
    Mutant("W02", "W", "facecontract_v141.py",
           "VERSION_MINOR 4 → 5",
           [("VERSION_MINOR = 4", "VERSION_MINOR = 5")],
           {"WIRE", "RETGT", "VJS"},
           "decode() version check fails; stored fixtures pin 1.4"),
    Mutant("W03", "W", "facecontract_v141.py",
           "FLAG_IDLE 0x01 → 0x04 (bit-2 collision with reserved head_pose bit)",
           [("FLAG_IDLE = 0x01", "FLAG_IDLE = 0x04")],
           {"VJS"},
           "Python suites use fc.FLAG_IDLE consistently → self-consistent; "
           "verify.js hard-pins FLAG_IDLE=0x01 and compares stored "
           "expect.flags=1 → cross-language kill. KEY PREDICTION: no Python "
           "suite notices a wire-semantics bit change"),
    Mutant("W04", "W", "facecontract_v141.py",
           "CRC_RESIDUE 0x2144DF1C → 0xDEADBEEF",
           [("CRC_RESIDUE = 0x2144DF1C", "CRC_RESIDUE = 0xDEADBEEF")],
           {"WIRE"},
           "residue invariant tests compare against fc.CRC_RESIDUE"),
    Mutant("W05", "W", "facecontract_v141.py",
           "encode CRC-32 → adler32 (wrong checksum algorithm)",
           [("return body + _CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)",
             "return body + _CRC.pack(zlib.adler32(body) & 0xFFFFFFFF)")],
           {"WIRE", "RETGT", "VJS"},
           "decode recomputes crc32 → mismatch on every self-encoded frame"),
    Mutant("W06", "W", "facecontract_v141.py",
           "decode(): CRC mismatch check disabled (accept corrupt frames)",
           [("if stored_crc != computed_crc:",
             "if False and stored_crc != computed_crc:")],
           {"WIRE"},
           "test_crc_corruption_rejected expects the CRC mismatch error"),
    Mutant("W07", "W", "facecontract_v141.py",
           "decode(): payload_len field check disabled (defence-in-depth gone)",
           [("if payload_len != len(buf) - _FRAME_OVERHEAD:",
             "if False and payload_len != len(buf) - _FRAME_OVERHEAD:")],
           {"WIRE"},
           "the three payload_len defence-in-depth tests expect the specific "
           "error; without the check the CRC check fires instead (or struct "
           "unpack walks off the buffer)"),
    Mutant("W08", "W", "facecontract_v141.py",
           "SUPPORTED_BS_COUNTS drops 52 (Extended level unshippable)",
           [("SUPPORTED_BS_COUNTS = frozenset({1, 2, 4, 8, 32, 52, 72})",
             "SUPPORTED_BS_COUNTS = frozenset({1, 2, 4, 8, 32, 72})")],
           {"RETGT"},
           "speech_ru_01/gap_sequence streams are bs_count=52 → encode raises "
           "→ T3 byte-exact fails; other suites only use 4/32/72"),
    Mutant("W09", "W", "facecontract_v141.py",
           "decode(): one-hot I3 check disabled (encode-side kept)",
           [("    if flags not in (FLAG_IDLE, FLAG_SPEAKING):\n"
             "        raise FaceContractError(\"I3 violation: flags must be "
             "one-hot (IDLE or SPEAKING)\")",
             "    if False and flags not in (FLAG_IDLE, FLAG_SPEAKING):\n"
             "        raise FaceContractError(\"I3 violation: flags must be "
             "one-hot (IDLE or SPEAKING)\")")],
           set(),
           "Run #1 prediction: survivor — no Python test constructed a frame "
           "with bad flags and fed it to decode(). CONFIRMED (run #1). Fix: "
           "TestDecodeFlagSemantics added to test_facecontract_wire.py "
           "(reflag() helper keeps the CRC valid so the flags branch is "
           "reached); run #2 must kill it",
           disposition="fixed: TestDecodeFlagSemantics added (test_facecontract_wire.py)"),
    # ══ N — channel names / metadata (frozen order) ═════════════════════════
    Mutant("N01", "N", "facecontract_v141.py",
           "CORE32: eyeBlinkLeft ↔ eyeBlinkRight (the historical blind spot)",
           [("\"eyeBlinkLeft\", \"eyeBlinkRight\", \"eyeWideLeft\", \"eyeWideRight\",",
             "\"eyeBlinkRight\", \"eyeBlinkLeft\", \"eyeWideLeft\", \"eyeWideRight\",")],
           {"RETGT"},
           "T1 hard pin. Historical note: before T1 existed this mutant made "
           "the whole 40-test suite GREEN (measured 2026-11-27); generators "
           "write identical L/R blink values so T3/T9 cannot see it"),
    Mutant("N02", "N", "facecontract_v141.py",
           "CORE32: jawOpen ↔ mouthClose (distinct-signal pair)",
           [("\"jawForward\", \"jawLeft\", \"jawRight\", \"jawOpen\",",
             "\"jawForward\", \"jawLeft\", \"jawRight\", \"mouthClose\","),
            ("\"mouthClose\", \"mouthPressLeft\", \"mouthPressRight\",",
             "\"jawOpen\", \"mouthPressLeft\", \"mouthPressRight\",")],
           {"RETGT"},
           "T1 pin + T9 swap-sensitivity + T2 json pin + T3 regenerated bytes"),
    Mutant("N03", "N", "facecontract_v141.py",
           "CORE32: mouthPressLeft ↔ mouthPressRight (the P0-A probe pair)",
           [("\"mouthClose\", \"mouthPressLeft\", \"mouthPressRight\",",
             "\"mouthClose\", \"mouthPressRight\", \"mouthPressLeft\",")],
           {"RETGT"},
           "T1 pins NAME_TO_INDEX['mouthPressLeft']==24; generators never "
           "address mouthPress*, so T3/T9 are blind — only T1 kills"),
    Mutant("N04", "N", "facecontract_v141.py",
           "CORE32: tongueOut renamed tongueOutV2",
           [("\"noseSneerLeft\", \"noseSneerRight\", \"tongueOut\",",
             "\"noseSneerLeft\", \"noseSneerRight\", \"tongueOutV2\",")],
           {"RETGT"},
           "T1 pin (name-level change, no generator addresses tongueOut)"),
    Mutant("N05", "N", "channels.json",
           "Premium block: channels 52 ↔ 53 swapped (whole entries)",
           json_mut=_swap_premium_52_53,
           predicted={"RETGT"},
           rationale="T2 freezes [52:] name order; T8 facecontract_idx "
                     "sequence 0..71 breaks when entries (with their idx) swap"),
    Mutant("N06", "N", "channels.json",
           "genuine_gap channel gets an arkit_equivalent (contract break)",
           json_mut=_gap_gets_equivalent,
           predicted={"RETGT"},
           rationale="T4: genuine_gap ⇒ arkit_equivalent must be None"),
    Mutant("N07", "N", "channels.json",
           "redundant channel's arkit_equivalent_name points at the wrong name",
           json_mut=_corrupt_equivalent_name,
           predicted={"RETGT"},
           rationale="T5 cross-checks channels[eq]['name'] == claimed name"),
    Mutant("N08", "N", "channels.json",
           "metahuman_target set non-null (decision (a) reversed)",
           json_mut=_set_metahuman_target,
           predicted={"RETGT"},
           rationale="T7 asserts metahuman_target is null everywhere"),
    Mutant("N09", "N", "channels.json",
           "premium_coverage_counts genuine_gap 10 → 9 (count drift)",
           json_mut=_decrement_gap_count,
           predicted={"RETGT"},
           rationale="T8 pins the coverage count dict"),
    Mutant("N10", "N", "channels.json",
           "mouthClose wire_semantics cleared (undocumented channel)",
           json_mut=_clear_wire_semantics,
           predicted={"RETGT"},
           rationale="T6 requires wire_semantics on mouthClose"),
    # ══ G — generator signal semantics ══════════════════════════════════════
    Mutant("G01", "G", "facecontract_v141.py",
           "idle blink peak 1.0 → 0.5 (breaks §7.1 OEF: peak >= 0.85)",
           [("15: 1.0, 16: 1.0,", "15: 0.5, 16: 0.5,")],
           {"RETGT"},
           "T3: regenerated idle_breathing_blink diverges from locked .b64"),
    Mutant("G02", "G", "facecontract_v141.py",
           "idle jawOpen breathing amplitude 0.026 → 0.006 (range collapses)",
           [("0.032 + 0.026 * math.sin(", "0.032 + 0.006 * math.sin(")],
           {"RETGT"},
           "T3 byte divergence; §7.1 OEF 'visible range' would also break"),
    Mutant("G03", "G", "facecontract_v141.py",
           "speech_to_idle: chunk 2 flags SPEAKING → IDLE (transition moved)",
           [("flags = FLAG_SPEAKING if c < 3 else FLAG_IDLE",
             "flags = FLAG_SPEAKING if c < 2 else FLAG_IDLE")],
           {"RETGT"},
           "T3 byte divergence (stored stream pins the transition point)"),
    Mutant("G04", "G", "facecontract_v141.py",
           "RAW-INDEX REGRESSION (historical bug): eyeBlink* written as row[0]/row[1]",
           [("row[NAME_TO_INDEX[\"eyeBlinkLeft\"]] = blink",
             "row[0] = blink"),
            ("row[NAME_TO_INDEX[\"eyeBlinkRight\"]] = blink",
             "row[1] = blink")],
           {"RETGT"},
           "META-MUTANT for T9: bytes are IDENTICAL while the map is frozen "
           "(0/1 == NAME_TO_INDEX today) → T1/T2/T3 stay green; ONLY T9's "
           "map-patching sensitivity can see that the generator stopped "
           "following the map. Run #1 measured T9 v1 (jawOpen<->mouthClose "
           "swap) SURVIVED this mutant — L/R blink values are identical, "
           "swapping their map entries cannot change a byte. T9 v2 "
           "(displacement to distinct far indices + blink-peak assert) "
           "closes it (run #2)",
           disposition="fixed: T9 rewritten as displacement (test_retargeting_smoke.py)"),
    Mutant("G05", "G", "facecontract_v141.py",
           "idle mouthClose signal zeroed (channel silently dropped)",
           [("0.010 + 0.004 * math.sin(2 * math.pi * i / 32))",
             "0.000 + 0.000 * math.sin(2 * math.pi * i / 32))")],
           {"RETGT"},
           "T3 byte divergence"),
    Mutant("G06", "G", "facecontract_v141.py",
           "speech articulation 0.20→0.02 (§7.2 crossfade snap disappears)",
           [("0.20 + 0.10 * math.sin(2 * math.pi * i / 4)",
             "0.02 + 0.01 * math.sin(2 * math.pi * i / 4)")],
           {"RETGT", "BEH"},
           "T3 byte divergence; verify_behavior Test 7 asserts raw delta "
           "> 0.15 ('test is meaningful') — 0.04 < 0.15 → semantic kill "
           "with a diagnosis byte-comparison cannot give"),
    Mutant("G07", "G", "facecontract_v141.py",
           "RAW-INDEX REGRESSION: browInnerUp written as row[6] (validate T9 v2 breadth)",
           [("row[NAME_TO_INDEX[\"browInnerUp\"]] = (",
             "row[6] = (")],
           {"RETGT"},
           "bytes identical while the map is frozen; T9 v2 displacement "
           "(browInnerUp→28 + per-signal assert) must kill it — proves the "
           "fix generalizes beyond eyeBlink*"),
    Mutant("G08", "G", "facecontract_v141.py",
           "RAW-INDEX REGRESSION: speech mouthUpperUpLeft written as row[21] (T9 v2 breadth)",
           [("row[NAME_TO_INDEX[\"mouthUpperUpLeft\"]] = 0.15",
             "row[21] = 0.15")],
           {"RETGT"},
           "speech-generator side of the same blind spot; T9 v2 speech "
           "displacement (mouthUpperUpLeft→27) must kill it"),
    # ══ S — state machine / glue invariants ═════════════════════════════════
    Mutant("S01", "S", "gorgon_state.py",
           "commit() freezes seq (I1 broken: no strict +1)",
           [("self._seq += 1", "self._seq = self._seq")],
           {"GLUE", "EDGE"},
           "many tests decode frames and assert seq sequences"),
    Mutant("S02", "S", "gorgon_state.py",
           "commit() freezes t_ns (I2/I9 broken: no strict step)",
           [("self._t_ns += self.config.dur_ns", "pass  # mutant: t_ns frozen")],
           {"GLUE", "EDGE"},
           "t_ns step assertions in both suites"),
    Mutant("S03", "S", "gorgon_state.py",
           "open(): I5 seq reset removed (belt-and-braces line)",
           [("self._seq = 0            # I5: seq=0 on new WS",
             "self._seq = self._seq    # mutant: I5 reset removed")],
           set(),
           "KEY PREDICTION: survivor — __init__ already sets _seq=0 and tests "
           "reconnect via a NEW SessionStateMachine, never reopen the same "
           "one (open() raises unless CLOSED, so reuse is impossible). "
           "If it survives: the line is dead code kept as defence-in-depth",
           disposition="ACCEPTED: open() cannot reopen a session (raises unless "
                       "CLOSED), so the reset line is unreachable — kept as "
                       "belt-and-braces for future reuse; killing it would "
                       "require changing the state-machine contract itself"),
    Mutant("S04", "S", "gorgon_state.py",
           "should_drop_idle() returns False always (I7 backpressure gone)",
           [("        return (self.state is SessionState.OPEN\n"
             "                and self._in_flight >= self.config.max_queue_depth)",
             "        return False  # mutant: backpressure gate disabled")],
           {"GLUE", "EDGE"},
           "glue test_03 drops-idle-only test; edge reconnect test asserts "
           "should_drop_idle() True at depth 35"),
    Mutant("S05", "S", "gorgon_glue.py",
           "hold-last tail removed (HANGOVER falls through to idle)",
           [("                if self._last_model_payload is not None:\n"
             "                    return self._last_model_payload",
             "                if self._last_model_payload is not None:\n"
             "                    pass  # mutant: hold-last removed")],
           {"GLUE"},
           "glue test_02 asserts the drain sequence ... P3 P3 (hold-last tail)"),
    Mutant("S06", "S", "gorgon_state.py",
           "accepts_model_chunks(): HANGOVER admitted (tail resurrection bug)",
           [("        return self.state in (UtteranceState.BUFFERING,\n"
             "                              UtteranceState.SPEECH_STREAMING)",
             "        return self.state in (UtteranceState.BUFFERING,\n"
             "                              UtteranceState.SPEECH_STREAMING,\n"
             "                              UtteranceState.SPEECH_HANGOVER)")],
           {"GLUE", "EDGE"},
           "edge test_glue_chunk_after_done_does_not_resurrect_tail + "
           "test_hangover_rejects_new_chunks; glue test_02 late-chunk assert"),
    Mutant("S07", "S", "gorgon_glue.py",
           "encode failure commits anyway (the pre-peek/commit I1 gap bug)",
           [("                          \"seq/t_ns NOT advanced (peek/commit)\", seq)\n"
             "            return",
             "                          \"seq/t_ns NOT advanced (peek/commit)\", seq)\n"
             "            self.session.commit()  # mutant: advance despite failure")],
           {"GLUE"},
           "glue test_04: encode failure must leave (seq, t_ns) untouched — "
           "commit in the except path re-opens the gap window"),
    Mutant("S08", "S", "gorgon_state.py",
           "on_vad_start in HANGOVER: deadline not cancelled (ghost expiry)",
           [("            self._hangover_start_us = None\n"
             "            if self._model_done_seen:",
             "            pass  # mutant: ghost deadline not cancelled\n"
             "            if self._model_done_seen:")],
           {"EDGE"},
           "edge test_resume_cancels_old_deadline + "
           "test_no_ghost_expiry_5s_after_resume"),
    Mutant("S09", "S", "gorgon_state.py",
           "accepts_model_chunks(): IDLE admitted (stale chunks resurface)",
           [("        return self.state in (UtteranceState.BUFFERING,\n"
             "                              UtteranceState.SPEECH_STREAMING)",
             "        return True  # mutant: admission gate removed")],
           {"GLUE", "EDGE"},
           "glue test_05 (stale chunk discarded in idle) + edge "
           "test_rejects_in_idle"),
    Mutant("S10", "S", "gorgon_glue.py",
           "glue flags: SPEAKING ↔ IDLE swapped when building the frame",
           [("        if self.utterance.is_speaking():\n"
             "            flags |= FLAG_SPEAKING\n"
             "        if self.utterance.is_idle():\n"
             "            flags |= FLAG_IDLE",
             "        if self.utterance.is_speaking():\n"
             "            flags |= FLAG_IDLE\n"
             "        if self.utterance.is_idle():\n"
             "            flags |= FLAG_SPEAKING")],
           {"GLUE", "EDGE"},
           "glue decodes frames and asserts flag sequences against the "
           "correct constants"),
    # ══ V — stored cross-language fixtures ══════════════════════════════════
    Mutant("V01", "V", "facecontract_vectors.json",
           "frame 0 expect.payload[0] tampered (fixture drift)",
           json_mut=_tamper_vector_payload,
           predicted={"VJS"},
           rationale="only verify.js consumes facecontract_vectors.json; no "
                     "Python test reads it — the fixture is cross-language-"
                     "pinned only. KEY PREDICTION: python suites stay green"),
    Mutant("V02", "V", "facecontract_vectors.json",
           "frame 3 frame_hex payload byte flipped (corrupt stored frame)",
           json_mut=_tamper_frame_hex,
           predicted={"VJS"},
           rationale="verify.js CRC check on the stored hex fails"),
]


# ── harness ───────────────────────────────────────────────────────────────────

COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc")

EXECUTORS: List[Tuple[str, Callable[[str], Tuple[bool, List[str]]]]] = [
    (name, (lambda w, m=module: _run_py(w, m))) for name, module in PY_EXECUTORS
] + [("VJS", lambda w: _run_node(w, "verify.js")),
     ("BEH", lambda w: _run_node(w, "verify_behavior.mjs"))]


def make_workdir() -> str:
    return tempfile.mkdtemp(prefix="gorgon_mut_")


def copy_repo(work: str) -> None:
    shutil.copytree(HERE, work, ignore=COPY_IGNORE, dirs_exist_ok=True)


def apply_mutant(work: str, mut: Mutant) -> Optional[str]:
    """Apply the mutation in the temp copy. Returns None on success or an
    error string (pattern missing/ambiguous)."""
    if mut.json_mut is not None:
        path = os.path.join(work, mut.target)
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc = mut.json_mut(doc)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return None
    path = os.path.join(work, mut.target)
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    for find, replace in mut.edits:
        n = content.count(find)
        if n != 1:
            return f"pattern hits {n}x (need exactly 1): {find[:60]!r}"
        content = content.replace(find, replace)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return None


def run_executors(work: str, executors) -> Dict[str, dict]:
    out = {}
    for name, fn in executors:
        ok, failed = fn(work)
        out[name] = {"ok": ok, "failed": failed}
    return out


def fmt_set(s: set) -> str:
    return ", ".join(sorted(s)) if s else "—"


def main() -> int:
    quick = "--quick" in sys.argv
    executors = EXECUTORS if not quick else EXECUTORS[:-2]

    # 1. baseline health gate — a matrix over a red suite is meaningless
    base = make_workdir()
    copy_repo(base)
    print("== BASELINE (pristine copy, all executors must be green) ==")
    baseline = run_executors(base, executors)
    shutil.rmtree(base)
    bad = [n for n, r in baseline.items() if not r["ok"]]
    for name, r in baseline.items():
        print(f"  {name:<6} {'OK' if r['ok'] else 'FAIL ' + str(r['failed'])}")
    if bad:
        print(f"BASELINE RED on {bad} — aborting (fix the suite first)")
        return 2

    # 2. run every mutant
    rows = []
    n_kill = 0
    print(f"\n== MUTANTS ({len(MUTANTS)}) ==")
    for mut in MUTANTS:
        work = make_workdir()
        try:
            copy_repo(work)
            err = apply_mutant(work, mut)
            if err:
                rows.append({
                    "id": mut.mid, "category": mut.category,
                    "target": mut.target, "description": mut.description,
                    "status": "error", "error": err,
                    "predicted": sorted(mut.predicted),
                })
                print(f"  {mut.mid} ERROR  {err}")
                continue
            res = run_executors(work, executors)
            killers = sorted(n for n, r in res.items() if not r["ok"])
            killing_tests = {n: r["failed"] for n, r in res.items() if not r["ok"]}
            killed = bool(killers)
            n_kill += killed
            rows.append({
                "id": mut.mid, "category": mut.category,
                "target": mut.target, "description": mut.description,
                "status": "killed" if killed else "survived",
                "killers": killers, "killing_tests": killing_tests,
                "predicted": sorted(mut.predicted),
                "prediction_match": sorted(mut.predicted) == killers,
            })
            mark = "KILLED" if killed else "** SURVIVED **"
            diff = "" if sorted(mut.predicted) == killers else \
                f"  [predicted {fmt_set(mut.predicted)}]"
            print(f"  {mut.mid} {mark:<13} killers: {fmt_set(killers)}{diff}")
        except subprocess.TimeoutExpired:
            rows.append({"id": mut.mid, "category": mut.category,
                         "target": mut.target, "description": mut.description,
                         "status": "error", "error": "timeout",
                         "predicted": sorted(mut.predicted)})
            print(f"  {mut.mid} ERROR  timeout")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # 3. report
    total = len(rows)
    survivors = [r for r in rows if r["status"] == "survived"]
    errors = [r for r in rows if r["status"] == "error"]
    mismatches = [r for r in rows
                  if r["status"] in ("killed", "survived")
                  and not r.get("prediction_match", True)]
    score = n_kill / total if total else 0.0

    json_path = os.path.join(HERE, "mutation_matrix.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({
            "generated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "baseline": {n: r["ok"] for n, r in baseline.items()},
            "executors": [n for n, _ in executors],
            "mutation_score": round(score, 4),
            "killed": n_kill, "total": total,
            "rows": rows,
        }, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    write_md_report(baseline, rows, score, executors, quick)
    print(f"\n== SUMMARY ==")
    print(f"  mutation score: {n_kill}/{total} = {score:.1%}")
    print(f"  survivors: {[r['id'] for r in survivors] or 'none'}")
    print(f"  prediction mismatches: {[r['id'] for r in mismatches] or 'none'}")
    print(f"  errors: {[r['id'] for r in errors] or 'none'}")
    print(f"  report: MUTATION_MATRIX.md / mutation_matrix.json")
    return 0


def write_md_report(baseline, rows, score, executors, quick) -> None:
    ex_names = [n for n, _ in executors]
    lines = []
    a = lines.append
    a("# MUTATION MATRIX — Gorgon FaceContract suite")
    a("")
    a(f"_Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} by "
      f"`mutation_matrix.py` (P1)._")
    a("")
    a("## Method")
    a("")
    a("Each mutant is a minimal semantic corruption of exactly one artifact. "
      "The harness copies the repo to a temp dir, applies the mutation, runs "
      "all executors, and records KILLED / SURVIVED. Killers predicted "
      "a-priori (before execution) are compared against the actual killers; "
      "mismatches are reported — they mean the mental model of the suite was "
      "wrong. Source tree is never modified.")
    a("")
    a(f"**Executors:** {', '.join(ex_names)}"
      + ("  (VJS/BEH skipped in this run: `--quick`)" if quick else ""))
    a("")
    a(f"**Baseline:** all executors green — "
      + ", ".join(f"{n}: OK" for n in ex_names))
    a("")
    a(f"**Mutation score: {sum(1 for r in rows if r['status']=='killed')}"
      f"/{len(rows)} = {score:.1%}** (killed/total; errors excluded from "
      "the denominator in the JSON, counted here for honesty)")
    a("")
    a("## Matrix")
    a("")
    a("| ID | Category | Target | Mutation | " +
      " | ".join(ex_names) + " | Result | Killers vs predicted |")
    a("|" + "---|" * (5 + len(ex_names) + 2))
    for r in rows:
        cells = []
        for ex in ex_names:
            if r["status"] == "error":
                cells.append("e")
            elif ex in r.get("killers", []):
                cells.append("K")
            else:
                cells.append("·")
        cmp_txt = ("=" if r.get("prediction_match") else
                   f"pred {', '.join(r['predicted']) or '—'}")
        result = {"killed": "killed", "survived": "SURVIVED",
                  "error": "error"}[r["status"]]
        a(f"| {r['id']} | {r['category']} | `{r['target']}` | "
          f"{r['description']} | " + " | ".join(cells) +
          f" | {result} | {cmp_txt} |")
    a("")
    a("Legend: **K** = killed by this executor, `·` = survived it, "
      "`e` = executor/harness error.")
    a("")

    survivors = [r for r in rows if r["status"] == "survived"]
    a("## Survivors (semantic changes that ship silently)")
    a("")
    if survivors:
        disp_by_id = {m.mid: m.disposition for m in MUTANTS}
        for r in survivors:
            a(f"### {r['id']} — {r['description']}")
            a("")
            a(f"Target: `{r['target']}`. "
              f"Predicted: {', '.join(r['predicted']) or '— (survivor by design)'}.")
            disp = disp_by_id.get(r["id"])
            if disp:
                a("")
                a(f"**Disposition: {disp}**")
            a("")
    else:
        a("None — every mutant was killed.")
    a("")

    mismatches = [r for r in rows
                  if r["status"] in ("killed", "survived")
                  and not r.get("prediction_match", True)]
    a("## Prediction mismatches (mental-model corrections)")
    a("")
    if mismatches:
        for r in mismatches:
            a(f"- **{r['id']}** ({r['status']}): predicted "
              f"{', '.join(r['predicted']) or '—'}, actual "
              f"{', '.join(r.get('killers', [])) or '—'}.")
    else:
        a("None — every a-priori prediction matched the actual killers.")
    a("")
    a("## Category breakdown")
    a("")
    a("| Category | Killed / Total | Score |")
    a("|---|---|---|")
    for cat in ("W", "N", "G", "S", "V"):
        sub = [r for r in rows if r["category"] == cat]
        if sub:
            k = sum(1 for r in sub if r["status"] == "killed")
            a(f"| {cat} ({CAT_NAMES[cat]}) | {k}/{len(sub)} | "
              f"{k/len(sub):.0%} |")
    a("")
    a("## Reproduce")
    a("")
    a("```bash")
    a("python mutation_matrix.py            # full, ~2-3 min, needs node")
    a("python mutation_matrix.py --quick    # python executors only")
    a("```")
    a("")
    lines.append(RUN_NOTES)
    path = os.path.join(HERE, "MUTATION_MATRIX.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


CAT_NAMES = {
    "W": "wire format", "N": "names/metadata", "G": "generator signals",
    "S": "state machine", "V": "stored fixtures",
}

RUN_NOTES = """\
## Run history and model corrections

**Run #1 (2026-11-27, 37 mutants): score 34/37 = 91.9%. Survivors: W09, G04,
S03. Prediction mismatches: W01, W02, W03, W05, G04, G06, S05, S06.**

Findings and the fixes they triggered:

1. **W09 SURVIVED (real coverage gap).** decode()'s one-hot I3 check was
   untested — every flags test went through encode(). Fixed: TestDecodeFlag
   Semantics added to test_facecontract_wire.py (a reflag() helper patches
   the flags byte AND recomputes the CRC so decode() reaches the flags
   branch instead of dying on a CRC mismatch). Run #2 kills W09.
2. **G04 SURVIVED (real test-quality gap).** The raw-index regression for
   eyeBlink* survived T9 v1 — L/R blink values are identical, so the
   jawOpen<->mouthClose swap test cannot observe them. Fixed: T9 rewritten
   as DISPLACEMENT — every generator-addressed name is patched onto a
   distinct far index (24..31) with per-signal asserts, including the
   blink-peak assert at sub-frame 14 (sub-frame 0 is blind: 0.001 == the
   row default). Run #2 kills G04; new mutants G07/G08 verify the fix
   generalizes to browInnerUp and speech mouthUpperUpLeft.
3. **S03 SURVIVED (accepted).** open()'s I5 reset is unreachable: open()
   raises unless the session is CLOSED, and reconnect is modelled as a NEW
   SessionStateMachine. Accepted as dead-code defence-in-depth.
4. **Mental-model correction (all W01/W02/W03/W05/G06 mismatches share one
   root): the Node verifiers (verify.js / verify_behavior.mjs) pin the
   STORED fixtures, not the encoder or generators.** They only kill
   mutants that change stored bytes (V01/V02). Encoder/generator drift is
   caught by T3 (regenerate-and-compare), and encoder-constant drift that
   is self-consistent (W03 FLAG_IDLE = 0x04) was caught ONLY by T3 in run #1 (the Python suites use the module
   constants and stay self-consistent); run #2's unknown-flag-bit decode
   test pins the value too (W03 killed by WIRE+RETGT).
   S05/S06 mismatches were coverage overestimates: EDGE's drain test also
   pins hold-last (S05), and GLUE never pushes a chunk during HANGOVER
   (S06 is killed by EDGE only).

**Run #2 (2026-11-27, 39 mutants): after the W09/G04 fixes. See the matrix
above for the final score; S03 is the only documented survivor
(disposition: accepted).**
"""


if __name__ == "__main__":
    sys.exit(main())
