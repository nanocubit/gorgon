# GorgonMetaHuman — Integration & Debug Guide (UE5 ⇄ bridges)

End-to-end bring-up and troubleshooting for consuming FaceContract v1.4.1
frames in UE5, over the two transports this repo ships:

| Transport | Producer | Consumer | Status |
|---|---|---|---|
| Plain WebSocket | `gorgon_ws_bridge.py` (repo root) | `UGorgonWebSocketClient` (this plugin) | **Primary, fully wired** |
| LiveKit Data Channel | `livekit_bridge.py` (`avatar.fc14.v1` topic) | custom UE WebRTC/LiveKit client (does not exist yet — Unreal has no official LiveKit SDK) | producer ready, UE consumer deferred |

Both transports emit **byte-identical frames**: one FaceContract frame per
binary message. Only the pipe differs.

## 1. End-to-end bring-up (WebSocket path)

```bash
# 1. Python side (no deps beyond the repo + websockets)
pip install "websockets>=10"
python gorgon_ws_bridge.py --port 8765 --bs-count 72
#    offline conformance first:  python gorgon_ws_bridge.py --self-test

# 2. UE side
#    copy Plugins/GorgonMetaHuman into <Project>/Plugins/, regenerate, build
#    place GorgonMetaHumanActor, set WebSocketUrl = ws://127.0.0.1:8765
#    assign the MetaHuman face SkeletalMeshComponent to MetaHumanFaceMesh
#    Play — idle breathing/blink starts immediately (frames flow right away)
```

Console commands on the bridge: `speech` (scripted utterance → SPEAKING →
crossfade → idle), `stop`, `trigger <name>` (TEXT JSON to the client),
`metrics` (per-connection Gorgon metrics), `quit`.

Expected wire numbers (default fps=30, chunk_size=4, one frame per tick
≈133.33 ms):

| bs_count | payload_len | frame bytes | level |
|---|---|---|---|
| 32 | 512 | **548** | Core |
| 52 | 832 | **868** | Extended |
| 72 | 1152 | **1188** | Premium |

Frame size mismatch on the wire is the single fastest integration probe:
`548` bytes arriving means Core-32; `1188` means Premium-72. Anything else
→ someone is speaking a different `--bs-count` than the decoder expects
(the decoder does not hard-pin bs_count — I6 cross-checks header vs payload,
so wrong counts fail as `bs_count mismatch` only if the header lies).

## 2. UE automation conformance tests

`FaceContractDecoderTests.cpp` pins golden vectors generated from the Python
canon (`test_vectors/ue5_golden_vectors.json`):

- `GorgonMetaHuman.FaceContractDecoder.PositiveVectors` — bs32 idle +
  bs32/52/72 speech frames: every header field, payload spot values
  (jawOpen=idx 10, eyeBlinkLeft=idx 0), CRC residue Check B (`0x2144DF1C`).
- `...NegativeVectors` — canon rejection order with exact error prefixes:
  corrupted CRC → `CRC mismatch`, bad magic, wrong version, unknown flag bit
  `0x04` (head_pose reserved for v1.5), zero flags → `I3 violation`,
  truncated payload → `payload_len mismatch` (§5.2: BEFORE CRC),
  lying bs_count header → `bs_count mismatch` (I6).
- `...StreamI1I2` — 6-frame stream: seq strict +1, t_ns strict step
  `133333333` ns.

Run in-editor: **Session Frontend → Automation → GorgonMetaHuman** (or
`UE.EditorAutomation` commandlet). These tests are pure — no network, no
world required. If any of them fails after a protocol touch, the decoder
drifted from `facecontract_v141.py` — do not "fix" the test, fix the decoder.

## 3. Debug checklist: symptom → cause

All error strings below come from `FFaceContractDecoder::Decode` and mirror
the Python canon precedence (length → magic → version → fps → chunk_size →
payload_len → CRC → flags → one-hot → multiples → bs_count):

| Log line | Root cause | Fix |
|---|---|---|
| `frame rejected (N bytes): frame too short` | non-frame binary on the socket (e.g. LiveKit signal protobuf or JSON sniffing) | binary channel must carry ONLY FaceContract frames |
| `bad magic` | producer is not GRG1 (proxy protocol bytes, gzip, UTF-8 BOM) | check producer; magic bytes must be `47 52 47 31` ("GRG1") |
| `unsupported version: X.Y` | producer on v1.5 draft / patched canon | v1.4.1 client rejects every other version by design (PROTOCOL §10) |
| `payload_len mismatch: header=N actual=M` | truncated frame (WS fragmentation mishandled) or stream re-split mid-frame | the client accumulates fragments until `bIsLast` — verify no manual re-framing |
| `CRC mismatch: stored=... computed=...` + residue diagnostic | corrupted payload (real bit rot) **or** big-endian CRC storage | residue ≠ `0x2144DF1C` and message-dependent → BE CRC; residue == constant but Check A failed → body/CRC offset bug |
| `unknown flag bits: 0x04` | producer set FLAG_HEAD_POSE (v1.5-reserved) | v1.4.1 has no head-pose block; reject is canon-correct |
| `I3 violation: flags must be one-hot` | producer emitted 0x00/0x03 | fix producer flag building (is_speaking/is_idle are disjoint) |
| `bs_count mismatch: header=X derived=Y` | header byte 7 lies vs payload | producer bug; also see wrong `--bs-count` above |
| frames decode but `seq` jumps | transport loss (TCP is ordered — gaps mean drops) | §6.2 policy: log + resume; `seq==0` after prior frames = reconnect epoch → filter reset + fade-in |
| idle starts with a visible jaw snap | producer lacks server-side crossfade | `gorgon_ws_bridge.py`/`livekit_bridge.py` fade from the hold-last tail (~200 ms); verify with `--self-test` "no snap" check |

WS-layer specifics (this plugin):

- `HandleBinaryMessage` accumulates fragments into `BinaryBuffer` until
  `bIsLast`, then decodes ONE frame per message. If the server ever batches
  multiple frames per binary message, only the first decodes — the producer
  must keep one-frame-per-message.
- `HandleTextMessage` parses JSON and only reacts to
  `{"type":"trigger_reaction","reaction":"..."}` — any other text is
  silently ignored (logs nothing). If triggers never fire in BP: check the
  bridge actually sent TEXT (not binary JSON) — `gorgon_ws_bridge.py` sends
  triggers on its per-client outbound queue once per tick (≤133 ms latency).
- No frames at all: `Gorgon WS: connected` in the log means the socket is
  up; if idle frames still don't arrive, the bridge session may be DRAINING
  (client stop) or the tick loop is starved — `metrics` on the bridge
  console prints `state_session`, `queue_depth`, `chunks_emitted`.

## 4. LiveKit path notes (`livekit_bridge.py`)

The bridge publishes with `reliable=True` on:

| Topic | Payload | Shape |
|---|---|---|
| `avatar.fc14.v1` | binary | exactly the same FaceContract frames |
| `avatar.triggers.v1` | JSON text | `{"type":"trigger_reaction","reaction":"...","session_id":"..."}` |
| `avatar.state.v1` | JSON text | `{"type":"state","session_id","user_external_id","utterance","speaking","seq_next","t_ns_next","queue_depth","chunks_emitted"}` |

A future UE consumer needs a LiveKit SDK for Unreal (custom WebRTC
integration — the signal channel speaks protobuf RTC, it is not a raw pipe)
and must subscribe to these topics; then the decode path above applies
unchanged. Audio→blendshape is currently the RMS placeholder
(`AudioToBlendshapeConverter`) — replace with the ML adapter behind the same
`on_model_chunk()` contract; the wire side does not change.

Pipeline caveats already encoded in the bridge (do not "simplify" away):

- the idle crossfade arms LAZILY inside `BreathingIdleGenerator.next()`:
  `gorgon_glue.tick()` clears `Gorgon._last_model_payload` on the
  SPEAKING→IDLE boundary tick BEFORE the idle generator runs, so the fade
  tail is sourced from `RecordingModelQueue.last_popped` (last RENDERED
  chunk), not from Gorgon internals;
- `mark_utterance_start()` cancels an in-progress fade — a leftover blend
  must never bleed into the next utterance's post-speech idle.

## 5. Known repo findings (2026-11-28)

- `speech_ru_01.fc14` and `gap_sequence.fc14` share the same whole-blob
  CRC-32 (`0x3FA1D6A6`) — a genuine CRC-32 collision between two
  conformance streams (verified: different bytes, equal CRC, XOR-delta is a
  CRC codeword). Cosmetic for the Golden CRC pin (the pin cannot
  distinguish those two streams), per-frame CRCs remain authoritative.
- CI is not wired (workflow creation 403) — the golden pins live in the
  local suites (`test_retargeting_smoke.py` T3, `gorgon_ws_bridge.py
  --self-test`, UE automation above).
