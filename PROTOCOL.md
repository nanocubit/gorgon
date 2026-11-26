# FaceContract v1.4.1 — Wire Protocol Specification

**Status:** LOCKED (2026-11-26)
**Wire version bytes:** `major=0x01, minor=0x04`
**Magic:** `GRG1` (0x47 0x52 0x47 0x31)
**Locked implementation (canon):** this repository —
`facecontract_v141.py` (encoder/decoder, self-test) + `verify.js` (independent
JS decoder, `LOCKED. 22 frames verified.`)

> Provenance note. An earlier sketch in the design thread used magic `FC14`
> and a 28-byte header. That sketch was never committed as bytes. The locked
> bytes in this repository are the canon: magic `GRG1`, 32-byte header,
> `payload_len` as u32 at offset 20, CRC over the whole body. This document
> describes THOSE bytes; lock = lock.

Wire-format changes require a `version` bump in the header AND a major bump of
this document. Patch changes (v1.4.1 → v1.4.2) are allowed only in the
"Client tuning" and "Operational guidance" sections, never in §1–§6, §9.

---

## 1. Frame layout (32-byte header)

All multi-byte integers are **little-endian**. All floats are IEEE-754
binary32 little-endian (`DataView.setFloat32(..., true)` / `struct '<f'`).

| Offset | Size | Field         | Notes                                                    |
|-------:|-----:|---------------|----------------------------------------------------------|
| 0x00   | 4    | magic         | `GRG1` — ASCII bytes                                     |
| 0x04   | 1    | version_major | must be `0x01`                                           |
| 0x05   | 1    | version_minor | must be `0x04`                                           |
| 0x06   | 1    | flags         | one-hot, see §2                                          |
| 0x07   | 1    | bs_count      | blendshape count, see §3                                 |
| 0x08   | 2    | fps           | u16, 30 for v1.4.1                                       |
| 0x0A   | 2    | chunk_size    | u16, frames per chunk, 4 for v1.4.1                      |
| 0x0C   | 4    | seq           | u32, monotonic per session, starts at 0 (§6.1)           |
| 0x10   | 4    | dur_us        | u32, **advisory** floor, see §4                          |
| 0x14   | 4    | payload_len   | u32, bytes of payload — defence-in-depth, see §5.2       |
| 0x18   | 8    | t_ns          | u64, frame start time — **ground truth**, see §4         |
| 0x20   | N    | payload       | `N/4` Float32 LE, row-major `[frame][bs]`                |
| 0x20+N | 4    | crc32         | u32 LE, IEEE CRC-32 over bytes `[0, 0x20+N)`             |

**Frame length** is fully determined by header fields:
`36 + payload_len`, and `payload_len == bs_count * chunk_size * 4`.

head_pose: **not present in v1.4.1.** `encode(..., head_pose=...)` raises by
design; flag bit 2 is reserved for the future head_pose block (planned v1.5,
wire bump required).

---

## 2. Flags

| Bit | Name            | Meaning                                                     |
|----:|-----------------|-------------------------------------------------------------|
| 0   | `FLAG_IDLE`     | avatar is in idle/listening state (may be emitted during BUFFERING — latency masking) |
| 1   | `FLAG_SPEAKING` | avatar is vocally articulating (SPEECH_STREAMING, SPEECH_HANGOVER) |
| 2   | *reserved*      | earmarked for `FLAG_HEAD_POSE` (v1.5); must be 0 in v1.4.1  |
| 3–7 | *reserved*      | must be 0; receivers MUST ignore                            |

`FLAG_IDLE` and `FLAG_SPEAKING` are **one-hot**: exactly one of them MUST be 1
in every emitted frame. Both set or both clear is a spec violation; `encode()`
and `decode()` both enforce this (I3).

---

## 3. Channel levels

| Level | `bs_count` | Channels   | Typical client                                            |
|------:|-----------:|------------|-----------------------------------------------------------|
| T1–T4 | 1, 2, 4, 8 | test-scale | conformance vectors, CI synthetic streams                 |
| 0     | 32         | Core       | MVP, low-bandwidth links                                  |
| 1     | 52         | +Extended  | ARKit-compatible rigs (ReadyPlayerMe, Character Creator)  |
| 2     | 72         | +Premium   | UE5 / MetaHuman                                           |

The production levels were adopted from the design thread; the test-scale
levels {1, 2, 4, 8} are kept so the locked conformance vectors (bs_count=4)
remain valid. Adding levels does not change the header layout.

**Core-32 channel order is FROZEN** and mirrors `CORE32_CHANNEL_NAMES` in
`facecontract_v141.py` (eyeBlinkLeft=0, eyeBlinkRight=1, eyeWideLeft=2, …,
tongueOut=31). Extended/Premium ordering is committed with the first real rig
(`channels.json`, v1.4.2 artifact). `payload[frame * bs_count + channel_idx]`
for frame ∈ [0, chunk_size).

A client MUST ignore channels absent from its morph target dictionary — a
Level-2 stream on a Level-1 rig produces partial animation, not an error.

### 3.1 Why 20, not 16 or 200 (added 2026-11-27; advisory clarification,
patch class per §10 — zero wire bytes, normative content of §1–§6 unchanged)

20 is a product decision, not a derivation from MetaHuman. The set covers
seven facial categories (eyelids, brows, nose, cheeks, chin, lips, tongue)
with 1–2 channels each. It is not claimed to be minimal, exhaustive, or
optimal: 15 would have covered the same categories without the redundant
synonyms, and 25 could add browWrinkle/noseTwist. Redundant channels are
kept as semantic conveniences and are revocable in a future minor version
via `deprecated: true` in `channels.json` — but are not removed now (lock).
Per-channel classification (exact per-channel fields in `channels.json`):

- **10 genuine gaps** — ARKit 52 cannot express these at all:
  `jawChinRaise`, `jawChinLower`, `tongueRaise`, `tongueLower`
  (speech-relevant), plus lower-priority for a speech avatar:
  `eyeLowerLidLower*`, `noseDilation*`, `cheekSuck*`.
- **4 composition alternatives** — ARKit expresses the effect, but only as
  a compound or via multi-channel composition; Premium provides direct
  control: `eyeUpperLidRaise*` (vs `eyeWide*` = widen+raise as one curve),
  `browRaise*` (vs `browInnerUp` + `browOuterUp*`).
- **6 redundant with Level 1** — an ARKit equivalent already exists:
  `cheekRaiserLower*` (≡ `cheekSquint*`), `mouthTight*` (≡ `mouthPress*`),
  `mouthLipsTogether` (≡ `mouthClose` = 1.0), `mouthLipsPart` (≡
  `mouthClose` = 0.0 at `jawOpen` = 0). Clients MAY ignore them without
  losing expressiveness; they are kept as semantic synonyms.

A Level-2 stream puts 72 channels on the wire — that is a wire-format fact,
not an effective-output claim: a client MAY consume only the 52 Level-1
channels. Wire order and indices remain frozen regardless of category.

### 3.2 What FaceContract is NOT

- Not "MetaHuman-compatible by name" — rig mapping is client-side
  retargeting (RETARGETING.md); `metahuman_target` is null everywhere in
  `channels.json` by decision.
- Not a superset of NVIDIA Audio2Face-3D — NVIDIA is narrow and deep:
  per NVIDIA docs, 16 tongue curves + 5 rig-specific tuning curves on top
  of ARKit 52 (68 effective outputs with tongue enabled, 52 without).
  FaceContract is broad and shallow: 20 Premium channels across seven
  categories, 1–2 per category, with no rig-specific tuning. This is a
  different trade-off, not universality. Clients needing deep tongue
  animation should use NVIDIA ACE, not FaceContract Level 2.
- Not a superset of any specific rig's control surface. Per Epic MetaHuman
  docs, MetaHuman ships 18 tongue-related Control Curves — more than this
  contract exposes (cited, not counted by us; see RETARGETING.md §4).

---

## 4. Timing semantics — **`t_ns` is ground truth**

### 4.1 Server behaviour

The session state machine advances t_ns ONLY in `Session.commit()`, by the
exact integer step:

```
dur_ns = (chunk_size * 1_000_000_000) // fps     # 133_333_333 for 4 @ 30 fps
dur_us = dur_ns // 1000                          # 133_333 — advisory FLOOR
```

`t_ns[N] - t_ns[0] == N * dur_ns` exactly — integer math, no accumulation
error (verified over 27 000 chunks = 1 h, see `test_gorgon_edge.py` E4).

### 4.2 Client MUST

- Use `t_ns[i+1] - t_ns[i]` for time reconstruction, NOT `dur_us * 1000`.
- Treat `dur_us` as a pre-allocation hint only (ring buffer sizing, jitter
  buffers). For 4 @ 30 fps it under-reports by 333 ns per chunk.
- NOT assume `dur_us * 1000 == actual_step_ns`. Measured drift of a naive
  client against the server stream: **~9 ms per hour** (8_991_000 ns),
  ~216 ms/day, ~1.5 s/week. The server stream itself lags wall-clock nominal
  time by only 9 000 ns per hour.

### 4.3 Why this is not a wire change

`dur_us` cannot represent 133_333_333 ns; renaming it `dur_ns` would break the
lock. Declaring it advisory is a documentation change — zero bytes move on the
wire. `verify.js` pins the stream step to `chunk_size * 1e9 / fps` AND checks
`dur_us == floor(step / 1000)`, so a server that drifts (steps by
`dur_us * 1000`) or a header that lies about the floor fails the gate.

---

## 5. CRC-32 invariants

CRC is **IEEE 802.3** (reflected poly `0xEDB88320`, init `0xFFFFFFFF`, final
xor `0xFFFFFFFF`), stored as u32 little-endian.

### 5.1 Two independent checks

**Check A (primary):** `crc32(frame[0 : 32+N])` must equal the stored CRC
field. Mismatch → reject frame.

**Check B (structural, free):** `crc32(ENTIRE frame including its CRC field)`
must equal

```
expected_residue_le = 0x2144DF1C
```

This holds for any frame that passed Check A, because
`crc32(M ‖ LE-crc32(M))` is constant for standard CRC-32.

**Endianness diagnostic (MEASURED 2026-11-26, supersede the review
prediction):** if an implementation stores the CRC big-endian, Check B fails
for every frame — but the resulting value is **message-dependent**, NOT the
byte-swapped constant `0x1CDF4421` predicted earlier. Measured:

| Message                          | BE-store residue |
|----------------------------------|------------------|
| `b"123456789"`                   | `0x3CC9742C`     |
| `speech_bs4` frame body          | `0x90045ECC`     |

So: a residue of exactly `0x2144DF1C` proves little-endian storage + integrity;
ANY other value with a passing Check A is impossible; a failing Check A plus a
residue that varies between frames points at an endianness bug. Log the
computed residue on failure (`test_facecontract_wire.py` pins the measured
values).

### 5.2 `payload_len` defence-in-depth

`payload_len@0x14` MUST equal `bs_count * chunk_size * 4`. `decode()` validates
this BEFORE the CRC, so the specific `payload_len mismatch` error fires even
when the CRC field is corrupted too. A frame where these disagree is a spec
violation — never "prefer one or the other". This catches:

- silent header corruption (bit-flip in `bs_count` or `payload_len`),
- implementation drift (someone changes `bs_count` logic, forgets `payload_len`),
- transit truncation (frame cut mid-stream → length disagrees with header).

Negative vectors: `test_facecontract_wire.py::TestPayloadLenDefenceInDepth`.

---

## 6. Session & sequencing rules

### 6.1 Per-session sequence

`seq` is per WebSocket session, not global. On new WS connect the server
constructs a NEW `Gorgon` (hence new `SessionStateMachine`):

- `seq = 0` (I5), `t_ns` starts at a fresh server-selected epoch (wall clock
  or monotonic — clients MUST NOT assume any relation to Unix epoch);
- queue accounting is NOT inherited (`test_gorgon_edge.py` E3).

### 6.2 Client policy on seq gaps

| Observed                   | Client action                                                       |
|----------------------------|---------------------------------------------------------------------|
| `seq == expected`          | normal                                                              |
| `expected < seq < expected+4` | reorder window: buffer up to 100 ms, then process in-order       |
| `seq >= expected + 4`      | gap: interpolate `expected..seq-1` from last-known payload, resume at `seq` |
| `seq < expected`           | late/stale: drop, log metric                                        |
| duplicate `seq`            | drop second occurrence                                              |

The server guarantees a gapless `seq` stream per session (I1) — gaps on the
wire mean transport loss, which is why the locked pipeline uses
peek/commit: nothing advances unless the frame was successfully encoded.

### 6.3 Reconnect mid-utterance

Server is stateless across sessions. On reconnect the server starts a fresh
session (`seq=0`, new epoch); the first chunks may already carry
`FLAG_SPEAKING=1`. **Client MUST apply a fade-in over ~200 ms** from its
last-known (or neutral) pose to the first received pose — no visual snap.

### 6.4 Server backpressure (operational)

When the send queue exceeds `max_queue_depth`, the server drops **idle-sourced
chunks only** (I7); model chunks are never dropped. Dropped ticks do not
advance `seq`/`t_ns`, so the client sees a gapless stream at a temporarily
lower chunk rate.

---

## 7. Client tuning (v1.4.2 candidates — non-wire)

### 7.1 One Euro Filter profiles

Initial values, now backed by measurement
(`verify_behavior.mjs` on `idle_breathing_blink.fc14`, 2026-11-26):

| Profile      | Channels                    | `fc_min` | `beta` |
|--------------|-----------------------------|---------:|-------:|
| Blink        | `eyeBlinkLeft/Right`        | 5.0      | 0.10   |
| Articulation | `jaw*`, `mouth*`, `tongueOut` | 1.5    | 0.03   |
| Static       | everything else             | 1.0      | 0.007  |

Measured on the locked vector: blink peak **0.9270** (threshold ≥ 0.85,
symmetric L/R), jawOpen filtered **[0.0060, 0.0531]**, mean 0.0310, range
0.0471; blink decayed to **0.0035** within 300 ms of the peak; non-active
channels ≤ 0.0010; no NaN/Inf. `beta=0.10` held up — the earlier worry that it
was optimistic did not materialize on this signal.

**Tuning is state-dependent:** in `FLAG_IDLE` chunks the articulation profile
degrades to static; in `FLAG_SPEAKING` it is mandatory.

### 7.2 Crossfade on speech→idle transition (MUST)

When the client receives a chunk with `FLAG_SPEAKING=0` after a run of
`FLAG_SPEAKING=1`, it MUST blend from the last rendered speaking pose to
the incoming idle pose over 200 ms (±50 ms).

**Reference implementation:** `CrossfadeFilter` in `facecontract_v141.mjs`.
Called per-frame: `processFrame(bsValues, isSpeaking, nowMs)`.
Applied **before** One Euro Filter (§7.1), not after.

**Abort on resume:** if `isSpeaking=true` arrives mid-crossfade (VAD resumed
during the blend), the client MUST abort the crossfade immediately — do not
blend toward idle. Reference implementation resets `_startMs = null` on this
edge case.

**Blend source:** the blend starts from the LAST SPEAKING pose, not from the
first idle frame — at `progress=0` the filter renders `_from` verbatim, so
capturing the first idle frame would let the raw snap pass through untouched
and blend idle into idle. This is pinned by Test 7 (below).

**Test coverage:** `verify_behavior.mjs` Test 7 (transition gate) and
Test 8 (abort-on-resume: `isSpeaking=true` mid-blend must return the raw
frame, `_startMs = null`, no mixing toward idle). Test vector:
`speech_to_idle.fc14` (6 chunks, frame 11→12 transition with raw
jawOpen delta 0.23; crossfaded max per-frame delta 0.0441 < 0.15,
blend active exactly 6 frames @ 30 fps, idle tail mean 0.0300 —
measured 2026-11-26).

Server-side this is unnecessary: server emits `hold-last-model` during
`SPEECH_HANGOVER`, so the last speech payload is stable. Client crossfade
is what visually smooths the transition.

---

## 8. Test vectors

Generated by `python facecontract_v141.py --vectors [dir]`. Each `.fc14` is a
concatenation of frames (no container). The streams are binary, and the API
used to seed this repository is text-only, so they are stored base64-encoded
as `test_vectors/*.fc14.b64`; restore with `python test_vectors/decode_b64.py`.
The encoder is deterministic — regeneration is byte-identical to the stored
locked vectors (verified over all six files).

| File                        | Frames | bs_count | Size   | Purpose                              |
|-----------------------------|-------:|---------:|-------:|--------------------------------------|
| `silence.fc14`              | 1      | 32       | 548    | minimal valid idle frame             |
| `speech_ru_01.fc14`         | 3      | 52       | 2604   | multi-frame utterance                |
| `gap_sequence.fc14`         | 3      | 52       | 2604   | seq 0,1,3 (simulated loss)           |
| `idle_breathing_blink.fc14` | 8      | 32       | 4384   | full idle cycle incl. blink          |
| `speech_to_idle.fc14`       | 6      | 32       | 3288   | SPEAKING→IDLE transition — §7.2 crossfade MUST (Test 7) |
| `corrupted_crc.fc14`        | 1      | 32       | 548    | negative: MUST be rejected (CRC)     |

`head_pose_premium.fc14` is deferred: FLAG_HEAD_POSE is reserved for v1.5.
A file whose byte length does not match §1 MUST be rejected with a specific
"frame length mismatch" error, not a generic "bad frame" (§5.2).

Consumers: `verify_behavior.mjs` (idle vector + crossfade Test 7);
`verify.js` pins the `speech_to_idle` stream (byte length, header fields,
flag pattern, per-chunk CRCs — the JS-side EXPECTED); the CI "Golden CRC pin"
locks the whole-blob CRC-32 of every stream.

NB (measured 2026-11-26): whole-blob CRC-32 is a lock constant, not an
identity — `speech_ru_01.fc14` and `gap_sequence.fc14` genuinely collide on
it (both `0x3FA1D6A6`, 2263 differing bytes, confirmed by zlib, binascii and
an independent JS CRC). The CI pin therefore anchors every constant to its
file name, not merely to its presence in the self-test output.

---

## 9. Server pipeline invariants (state-diagram §4, locked)

| ID  | Invariant            | Enforcement point                                        |
|-----|----------------------|----------------------------------------------------------|
| I1  | seq strict +1        | `Session.commit()` — single advance point, after encode  |
| I2  | t_ns strict step     | `commit()` adds exact `dur_ns`                           |
| I3  | flags one-hot        | `is_speaking()/is_idle()` total+disjoint; encode/decode validate |
| I4  | CRC valid            | `encode()` atomic; frame enqueued only on success        |
| I5  | seq=0 on new WS      | Gorgon per WebSocket; `open()` resets                    |
| I6  | bs_count consistent  | `Gorgon.__init__` + header/length cross-check in decode  |
| I7  | no speech drop       | backpressure drops idle-sourced chunks only              |
| I8  | no half-chunk        | one frame per tick, selected atomically                  |
| I9  | t_ns monotone        | no setter for `_t_ns` at all                             |

Model-chunk admission (`accepts_model_chunks()`): chunks are accepted ONLY in
BUFFERING and SPEECH_STREAMING. In IDLE they are stale output; in HANGOVER the
hold-last tail is authoritative. Chunks queued BEFORE hangover still drain.

---

## 10. Versioning policy

| Change type                                    | Bump            |
|------------------------------------------------|-----------------|
| Channel reorder, header layout, CRC algorithm  | major + wire    |
| New flag bit (e.g. head_pose), new block       | minor + wire    |
| Advisory clarifications, client tuning         | patch, no wire  |
| Test vector additions, docs                    | patch, no wire  |
| New supported bs_count level (superset)        | patch, no wire  |

Current: **spec 1.4.1 / wire 0x01 0x04**. v1.4.2 is reserved for empirical OEF
tuning, `channels.json`, the §3.1/§3.2 coverage classification and
RETARGETING.md (advisory docs, patch class); no wire change expected.
head_pose is a v1.5 change (new flag bit + new block = minor + wire bump
per this table), never a patch.
