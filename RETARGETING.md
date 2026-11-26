# RETARGETING — mapping FaceContract channels to real rigs

**Status:** v1.4.2 draft artifact (2026-11-27). Advisory documentation —
zero wire bytes. `metahuman_target` is null everywhere in `channels.json`
by decision (a): **the server never maps; mapping is client-side.** This
file is the client-side guide, unverified by any real rig so far — treat
every rig-specific line below as a plan, not a measurement.

## 1. What maps by name, what does not

| Range | Channels | Mapping |
|---|---|---|
| idx 0–31 (Core, level 0) | 32 | Order **frozen** (`CORE32_CHANNEL_NAMES`, PROTOCOL §3). 31 of 32 carry ARKit canonical names → `direct_by_name`. `tongueOut` (idx 31) has no ARKit name. |
| idx 32–51 (Extended, level 1) | 20 | ARKit canonical names absent from Core-32, in ARKit canonical relative order → `direct_by_name`. |
| idx 52–71 (Premium, level 2) | 20 | FaceContract extension. No ARKit name; per-channel `coverage` in `channels.json`. |

A client MUST ignore channels absent from its morph target dictionary
(PROTOCOL §3) — a Level-2 stream on a Level-1 rig produces partial
animation, not an error.

## 2. ARKit-compatible rigs (ReadyPlayerMe, Character Creator, ARKit-52 rigs)

Level 1 is a superset of ARKit 52: 51 ARKit names + `tongueOut`.
Retargeting is identity-by-name; the only decision is what to do with
`tongueOut` (see §4).

## 3. Premium-20: what to implement first

Coverage classification (channels.json, `premium_coverage_counts`:
10 genuine gaps [6 low-priority], 4 composition alternatives,
6 redundant with Level 1):

1. **Implement first (speech-relevant genuine gaps):** `jawChinRaise`,
   `jawChinLower`, `tongueRaise`, `tongueLower` — ARKit cannot express
   independent chin or vertical tongue motion at all.
2. **Implement when the rig supports it (composition alternatives):**
   `eyeUpperLidRaise*`, `browRaise*` — the rig gets direct control where
   ARKit composes (`eyeWide*`, `browInnerUp` + `browOuterUp*`).
3. **May skip entirely (redundant synonyms):** `cheekRaiserLower*`,
   `mouthTight*`, `mouthLipsTogether`, `mouthLipsPart` — the ARKit
   equivalents (`cheekSquint*`, `mouthPress*`, `mouthClose`) already cover
   the semantics. Clients MAY ignore these without losing expressiveness.
4. **Low priority (genuine gaps, rarely needed in speech):**
   `eyeLowerLidLower*`, `noseDilation*`, `cheekSuck*`.

## 4. tongueOut — actionable guidance

- **What the channel is:** FaceContract `tongueOut` (idx 31, Core) is tongue
  protrusion, matching the colloquial "stick the tongue out" pose. It is the
  only tongue channel at Level 1; `tongueRaise`/`tongueLower` (Premium,
  idx 70–71) add vertical control that ARKit does not have at all.
- **Rig WITH tongue morphs:** drive `tongueOut` directly into the rig's
  tongue-out/protrusion morph; route `tongueRaise`/`tongueLower` into the
  vertical tongue curves if they exist.
- **Rig WITHOUT tongue articulation (typical MVP rig):** ignore all three.
  Do **not** fold `tongueOut` into `jawOpen` — that double-counts jaw
  articulation and produces the "mumbling" artifact (jaw drops without an
  audible phoneme). The client MUST-ignore rule (PROTOCOL §3) exists exactly
  for this case.
- **MetaHuman rigs:** per Epic MetaHuman docs, MetaHuman ships 18
  tongue-related Control Curves (cited, not counted by us). They are
  rig-internal Control Rig curves, not ARKit blendshapes — mapping them
  requires a curve-level client mapping. That mapping is deliberately
  **deferred**: `metahuman_target` stays null until the first real
  MetaHuman rig commits it. Until then a MetaHuman client should consume
  Level 1 (identity names) and ignore the tongue Premium channels.
- **NVIDIA Audio2Face-3D as a source:** per NVIDIA docs, A2F adds 16 tongue
  curves on top of ARKit 52 (plus 5 rig-specific tuning curves — 68 effective
  outputs with tongue enabled, 52 without). Same discipline: a client-side
  curve mapping, not a wire claim; FaceContract does not claim to be a
  superset of A2F (PROTOCOL §3.2).

## 5. One Euro Filter before rig mapping

Apply the §7.1 OEF profiles **after** crossfade (§7.2) and **before** rig
mapping, per channel profile (blink / articulation / static). Feeding raw
values into rig curves wastes the calibrated smoothing the contract pins in
`verify_behavior.mjs`.
