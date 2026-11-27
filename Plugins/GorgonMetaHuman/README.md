# GorgonMetaHuman — UE5 plugin

FaceContract v1.4.1 (magic `GRG1`) streaming into UE5 MetaHuman over plain
WebSocket. Consumes the byte-exact frames produced by `gorgon_ws_bridge.py`
(repo root). This plugin replaces the earlier integration draft — the draft
non-conformant in several load-bearing ways, see **Fixes vs the draft**.

## What this plugin deliberately does NOT do (read first)

- **No LiveKit.** The LiveKit signal WebSocket (`ws://<server>:7880`) speaks
  the LiveKit RTC signal protocol (protobuf join/offer/answer/tracks) — it is
  not a raw binary pipe. LiveKit keeps AUDIO/VIDEO; the FaceContract data
  plane is the repo's WebSocket bridge. (A LiveKit DataChannel path — Python
  server SDK `publish_data()` plus a custom UE WebRTC client — is a separate
  integration; Unreal has no official LiveKit SDK.)
- **No head pose.** v1.4.1 has no head-pose block; flag bit 2 is reserved for
  v1.5 (PROTOCOL §10, minor + wire bump). A frame carrying it is rejected as
  an unknown flag bit — same as the Python canon.
- **No tongue rig.** MetaHuman tongue = 18 Control-Rig curves (cited in
  RETARGETING.md §4); the curve mapping is deferred until committed by a real
  rig (`metahuman_target` stays null in channels.json). `tongueOut` is NEVER
  folded into `jawOpen`.

## Layout

```
GorgonMetaHuman.uplugin
Source/GorgonMetaHuman/
├── GorgonMetaHuman.Build.cs          # Core/CoreUObject/Engine/WebSockets/Json — no MetaHuman module dep
├── Public/
│   ├── GorgonMetaHuman.h             # module shim
│   ├── FaceContractDecoder.h         # USTRUCT frame + FFaceContractDecoder (canon mirror)
│   ├── GorgonWebSocketClient.h       # UCLASS transport (dynamic delegates, BP-bindable)
│   ├── MetaHumanRetargetComponent.h  # pipeline: crossfade → fade-in → OEF → mapping
│   └── GorgonMetaHumanActor.h        # wiring actor
└── Private/ (matching .cpp files)
```

## Conformance

`FFaceContractDecoder` mirrors `facecontract_v141.py::decode()` error
precedence exactly:

```
length → magic → version → fps → chunk_size → payload_len (BEFORE CRC, §5.2)
→ CRC (+ §5.1 residue diagnostic) → unknown flags → one-hot (I3)
→ chunk multiples → bs_count cross-check (I6)
```

`UMetaHumanRetargetComponent` implements the client tuning the contract pins:

- **§6.2** seq policy: stale/duplicate → drop; session epoch (`seq==0` after
  prior frames) → filter reset + fade-in; gap → log + resume (interpolation
  deferred; TCP is ordered, gaps mean transport loss).
- **§6.3** reconnect fade-in: ~200 ms blend from the last rendered pose.
- **§7.1** One Euro Filter profiles — blink `fc_min=5.0, beta=0.10`;
  articulation `1.5/0.03` (jaw\*, mouth\*, tongueOut); static `1.0/0.007`.
  In `FLAG_IDLE` chunks the articulation profile degrades to static.
- **§7.2** crossfade (MUST): 200 ms on SPEAKING→IDLE, blend source = last
  SPEAKING pose, abort-on-resume — a faithful port of `CrossfadeFilter` in
  `facecontract_v141.mjs` (Tests 7/8 in `verify_behavior.mjs`).
- Sub-frames are **played out at the stream fps** by `TickComponent` (a chunk
  is 4 sub-frames @30 fps), not dumped at once; OEF dt is real render time.
- CRC residue diagnostic on every rejected frame (`0x2144DF1C` LE constant,
  message-dependence points at big-endian storage — PROTOCOL §5.1).

## Channel mapping (channels.json blob `25705b7c…`)

| idx | action | note |
|---|---|---|
| 0–30, 32–51 | Direct | ARKit names → same-name morph targets (Core-32 frozen order + Extended-20) |
| 52, 53 | Composition | `eyeUpperLidRaise*` → `eyeWide*` (weight 1.0) |
| 56, 57 | Composition | `browRaise*` → `browInnerUp` + `browOuterUp*` (0.5/0.5 — advisory tuning) |
| 31, 70, 71 | Ignore | tongue — MetaHuman tongue is Control-Rig curves, deferred |
| 64, 65 | Ignore | `jawChinRaise/Lower` — genuine gaps; need Control-Rig curves, deferred |
| 54, 55, 58–61 | Ignore | genuine gaps, low priority for speech |
| 62, 63, 66–69 | Ignore | `redundant_with_arkit` — ARKit equivalents already drive the morphs |

Composition weights are **client tuning** (channels.json carries none) —
additive onto the ARKit compound morphs, clamped to [0,1].

## Install & run

```bash
# 1. Copy the plugin
cp -r Plugins/GorgonMetaHuman <YourUEProject>/Plugins/
# 2. Regenerate project files, build (UE 5.3+)
# 3. Start the bridge (repo root)
pip install "websockets>=10"
python gorgon_ws_bridge.py --port 8765 --bs-count 72
#    commands: speech | stop | trigger curious | metrics | quit
#    offline conformance: python gorgon_ws_bridge.py --self-test
```

In the editor:

1. Place `GorgonMetaHumanActor` (C++ class, Blueprintable) on the level.
2. Details → **Gorgon**: `WebSocketUrl = ws://127.0.0.1:8765`, assign the
   MetaHuman face `SkeletalMeshComponent` to `MetaHumanFaceMesh`.
3. Play. Idle breathing/blink starts immediately; type `speech` in the
   bridge console for an utterance (SPEAKING → §7.2 crossfade → idle).
4. Reactions to triggers (`trigger curious`) are designer territory:
   implement the **On Trigger Received** event in your actor Blueprint.

## Fixes vs the earlier draft

1. **Channel order was scrambled.** The draft started with eyeLook\*/eyeWide
   at 0–13; the canon puts `eyeBlinkLeft=0 … tongueOut=31` (FROZEN,
   PROTOCOL §3), Extended at 32–51, Premium at 52–71. Wrong order = every
   channel drives the wrong morph.
2. **Head pose removed.** The draft decoded a `FLAG_HEAD_POSE` block; v1.4.1
   has no such block (reserved v1.5).
3. **Version check added** (`version_major=1, version_minor=4`) — the draft
   accepted any version.
4. **Validation order fixed**: `payload_len` cross-check BEFORE the CRC
   (§5.2), then CRC, then flags/one-hot — the draft checked CRC last and
   never validated `payload_len == bs_count·chunk_size·4`.
5. **Chunk semantics fixed.** The draft treated the payload as 72 floats;
   it is `chunk_size` sub-frames of `bs_count` floats, row-major — the draft
   would have animated nonsense at bs_count=72/52/32.
6. **C++ API fixes**: `UMorphTarget::FindCurve` / `SetMorphTarget(Names[MorphIdx], v)`
   replaced by `USkeletalMesh::GetMorphTargetIndex(FName)` +
   `SetMorphTarget(FName, Value)`; `static constexpr TArray` (illegal) →
   static const C array; `RootComponent = <UActorComponent>` → USceneComponent
   root; dynamic delegates made legal (USTRUCT payload, UObject client).
7. **One Euro Filter** replaced the draft's non-One-Euro alpha/beta blend
   with the standard filter using the §7.1 measured profiles.
8. **Client MUSTs implemented**: §7.2 crossfade + abort-on-resume, §6.3
   fade-in, §6.2 seq policy, fps-paced sub-frame playout (§4).

## Deferred

- MetaHuman tongue Control Curves (idx 31, 70, 71) — needs a committed
  curve-level mapping on a real rig (RETARGETING.md §4).
- `jawChinRaise/Lower` morphs (idx 64, 65) — same Control-Rig prerequisite.
- §6.2 gap interpolation — MVP logs and resumes; a reorder buffer is not
  needed on ordered TCP.
