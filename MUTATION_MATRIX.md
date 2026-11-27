# MUTATION MATRIX — Gorgon FaceContract suite

_Generated 2026-11-27 14:54 UTC by `mutation_matrix.py` (P1)._

## Method

Each mutant is a minimal semantic corruption of exactly one artifact. The harness copies the repo to a temp dir, applies the mutation, runs all executors, and records KILLED / SURVIVED. Killers predicted a-priori (before execution) are compared against the actual killers; mismatches are reported — they mean the mental model of the suite was wrong. Source tree is never modified.

**Executors:** WIRE, GLUE, EDGE, RETGT, VJS, BEH

**Baseline:** all executors green — WIRE: OK, GLUE: OK, EDGE: OK, RETGT: OK, VJS: OK, BEH: OK

**Mutation score: 38/39 = 97.4%** (killed/total; errors excluded from the denominator in the JSON, counted here for honesty)

## Matrix

| ID | Category | Target | Mutation | WIRE | GLUE | EDGE | RETGT | VJS | BEH | Result | Killers vs predicted |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W01 | W | `facecontract_v141.py` | MAGIC b"GRG1" → b"GRG2" (encode writes, decode rejects) | K | · | · | K | · | · | killed | pred RETGT, VJS, WIRE |
| W02 | W | `facecontract_v141.py` | VERSION_MINOR 4 → 5 | K | · | · | K | · | · | killed | pred RETGT, VJS, WIRE |
| W03 | W | `facecontract_v141.py` | FLAG_IDLE 0x01 → 0x04 (bit-2 collision with reserved head_pose bit) | K | · | · | K | · | · | killed | pred VJS |
| W04 | W | `facecontract_v141.py` | CRC_RESIDUE 0x2144DF1C → 0xDEADBEEF | K | · | · | · | · | · | killed | = |
| W05 | W | `facecontract_v141.py` | encode CRC-32 → adler32 (wrong checksum algorithm) | K | K | K | K | · | · | killed | pred RETGT, VJS, WIRE |
| W06 | W | `facecontract_v141.py` | decode(): CRC mismatch check disabled (accept corrupt frames) | K | · | · | · | · | · | killed | = |
| W07 | W | `facecontract_v141.py` | decode(): payload_len field check disabled (defence-in-depth gone) | K | · | · | · | · | · | killed | = |
| W08 | W | `facecontract_v141.py` | SUPPORTED_BS_COUNTS drops 52 (Extended level unshippable) | · | · | · | K | · | · | killed | = |
| W09 | W | `facecontract_v141.py` | decode(): one-hot I3 check disabled (encode-side kept) | K | · | · | · | · | · | killed | pred — |
| N01 | N | `facecontract_v141.py` | CORE32: eyeBlinkLeft ↔ eyeBlinkRight (the historical blind spot) | · | · | · | K | · | · | killed | = |
| N02 | N | `facecontract_v141.py` | CORE32: jawOpen ↔ mouthClose (distinct-signal pair) | · | · | · | K | · | · | killed | = |
| N03 | N | `facecontract_v141.py` | CORE32: mouthPressLeft ↔ mouthPressRight (the P0-A probe pair) | · | · | · | K | · | · | killed | = |
| N04 | N | `facecontract_v141.py` | CORE32: tongueOut renamed tongueOutV2 | · | · | · | K | · | · | killed | = |
| N05 | N | `channels.json` | Premium block: channels 52 ↔ 53 swapped (whole entries) | · | · | · | K | · | · | killed | = |
| N06 | N | `channels.json` | genuine_gap channel gets an arkit_equivalent (contract break) | · | · | · | K | · | · | killed | = |
| N07 | N | `channels.json` | redundant channel's arkit_equivalent_name points at the wrong name | · | · | · | K | · | · | killed | = |
| N08 | N | `channels.json` | metahuman_target set non-null (decision (a) reversed) | · | · | · | K | · | · | killed | = |
| N09 | N | `channels.json` | premium_coverage_counts genuine_gap 10 → 9 (count drift) | · | · | · | K | · | · | killed | = |
| N10 | N | `channels.json` | mouthClose wire_semantics cleared (undocumented channel) | · | · | · | K | · | · | killed | = |
| G01 | G | `facecontract_v141.py` | idle blink peak 1.0 → 0.5 (breaks §7.1 OEF: peak >= 0.85) | · | · | · | K | · | · | killed | = |
| G02 | G | `facecontract_v141.py` | idle jawOpen breathing amplitude 0.026 → 0.006 (range collapses) | · | · | · | K | · | · | killed | = |
| G03 | G | `facecontract_v141.py` | speech_to_idle: chunk 2 flags SPEAKING → IDLE (transition moved) | · | · | · | K | · | · | killed | = |
| G04 | G | `facecontract_v141.py` | RAW-INDEX REGRESSION (historical bug): eyeBlink* written as row[0]/row[1] | · | · | · | K | · | · | killed | = |
| G05 | G | `facecontract_v141.py` | idle mouthClose signal zeroed (channel silently dropped) | · | · | · | K | · | · | killed | = |
| G06 | G | `facecontract_v141.py` | speech articulation 0.20→0.02 (§7.2 crossfade snap disappears) | · | · | · | K | · | · | killed | pred BEH, RETGT |
| G07 | G | `facecontract_v141.py` | RAW-INDEX REGRESSION: browInnerUp written as row[6] (validate T9 v2 breadth) | · | · | · | K | · | · | killed | = |
| G08 | G | `facecontract_v141.py` | RAW-INDEX REGRESSION: speech mouthUpperUpLeft written as row[21] (T9 v2 breadth) | · | · | · | K | · | · | killed | = |
| S01 | S | `gorgon_state.py` | commit() freezes seq (I1 broken: no strict +1) | · | K | K | · | · | · | killed | = |
| S02 | S | `gorgon_state.py` | commit() freezes t_ns (I2/I9 broken: no strict step) | · | K | K | · | · | · | killed | = |
| S03 | S | `gorgon_state.py` | open(): I5 seq reset removed (belt-and-braces line) | · | · | · | · | · | · | SURVIVED | = |
| S04 | S | `gorgon_state.py` | should_drop_idle() returns False always (I7 backpressure gone) | · | K | K | · | · | · | killed | = |
| S05 | S | `gorgon_glue.py` | hold-last tail removed (HANGOVER falls through to idle) | · | K | K | · | · | · | killed | pred GLUE |
| S06 | S | `gorgon_state.py` | accepts_model_chunks(): HANGOVER admitted (tail resurrection bug) | · | · | K | · | · | · | killed | pred EDGE, GLUE |
| S07 | S | `gorgon_glue.py` | encode failure commits anyway (the pre-peek/commit I1 gap bug) | · | K | · | · | · | · | killed | = |
| S08 | S | `gorgon_state.py` | on_vad_start in HANGOVER: deadline not cancelled (ghost expiry) | · | · | K | · | · | · | killed | = |
| S09 | S | `gorgon_state.py` | accepts_model_chunks(): IDLE admitted (stale chunks resurface) | · | K | K | · | · | · | killed | = |
| S10 | S | `gorgon_glue.py` | glue flags: SPEAKING ↔ IDLE swapped when building the frame | · | K | K | · | · | · | killed | = |
| V01 | V | `facecontract_vectors.json` | frame 0 expect.payload[0] tampered (fixture drift) | · | · | · | · | K | · | killed | = |
| V02 | V | `facecontract_vectors.json` | frame 3 frame_hex payload byte flipped (corrupt stored frame) | · | · | · | · | K | · | killed | = |

Legend: **K** = killed by this executor, `·` = survived it, `e` = executor/harness error.

## Survivors (semantic changes that ship silently)

### S03 — open(): I5 seq reset removed (belt-and-braces line)

Target: `gorgon_state.py`. Predicted: — (survivor by design).

**Disposition: ACCEPTED: open() cannot reopen a session (raises unless CLOSED), so the reset line is unreachable — kept as belt-and-braces for future reuse; killing it would require changing the state-machine contract itself**


## Prediction mismatches (mental-model corrections)

- **W01** (killed): predicted RETGT, VJS, WIRE, actual RETGT, WIRE.
- **W02** (killed): predicted RETGT, VJS, WIRE, actual RETGT, WIRE.
- **W03** (killed): predicted VJS, actual RETGT, WIRE.
- **W05** (killed): predicted RETGT, VJS, WIRE, actual EDGE, GLUE, RETGT, WIRE.
- **W09** (killed): predicted —, actual WIRE.
- **G06** (killed): predicted BEH, RETGT, actual RETGT.
- **S05** (killed): predicted GLUE, actual EDGE, GLUE.
- **S06** (killed): predicted EDGE, GLUE, actual EDGE.

## Category breakdown

| Category | Killed / Total | Score |
|---|---|---|
| W (wire format) | 9/9 | 100% |
| N (names/metadata) | 10/10 | 100% |
| G (generator signals) | 8/8 | 100% |
| S (state machine) | 9/10 | 90% |
| V (stored fixtures) | 2/2 | 100% |

## Reproduce

```bash
python mutation_matrix.py            # full, ~2-3 min, needs node
python mutation_matrix.py --quick    # python executors only
```

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
