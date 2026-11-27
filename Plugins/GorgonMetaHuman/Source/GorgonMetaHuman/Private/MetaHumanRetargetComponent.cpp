// MetaHumanRetargetComponent.cpp — see header for the mapping policy.
#include "MetaHumanRetargetComponent.h"

#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/PlatformTime.h"

// ── channels.json channel table (blob 25705b7c…; PROTOCOL §3) ────────────────
// Core-32 order is FROZEN (mirrors CORE32_CHANNEL_NAMES in
// facecontract_v141.py); Extended-20 is ARKit canonical relative order;
// Premium-20 coverage per channels.json. If channels.json ever changes,
// this table must change with it — the repo smoke suite guards the Python
// side; treat a mismatch here as a defect.
// clang-format off
const UMetaHumanRetargetComponent::FChannelRule
UMetaHumanRetargetComponent::ChannelRules[UMetaHumanRetargetComponent::FC_NUM_CHANNELS] =
{
    // idx 0–30 — Core-32, direct ARKit names (FROZEN order)
    { TEXT("eyeBlinkLeft"),        EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 0
    { TEXT("eyeBlinkRight"),       EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 1
    { TEXT("eyeWideLeft"),         EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 2
    { TEXT("eyeWideRight"),        EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 3
    { TEXT("browDownLeft"),        EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 4
    { TEXT("browDownRight"),       EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 5
    { TEXT("browInnerUp"),         EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 6
    { TEXT("jawForward"),          EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 7
    { TEXT("jawLeft"),             EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 8
    { TEXT("jawRight"),            EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 9
    { TEXT("jawOpen"),             EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 10
    { TEXT("mouthFunnel"),         EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 11
    { TEXT("mouthPucker"),         EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 12
    { TEXT("mouthSmileLeft"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 13
    { TEXT("mouthSmileRight"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 14
    { TEXT("mouthFrownLeft"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 15
    { TEXT("mouthFrownRight"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 16
    { TEXT("mouthStretchLeft"),    EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 17
    { TEXT("mouthStretchRight"),   EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 18
    { TEXT("mouthLowerDownLeft"),  EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 19
    { TEXT("mouthLowerDownRight"), EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 20
    { TEXT("mouthUpperUpLeft"),    EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 21
    { TEXT("mouthUpperUpRight"),   EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 22
    { TEXT("mouthClose"),          EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 23
    { TEXT("mouthPressLeft"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 24
    { TEXT("mouthPressRight"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 25
    { TEXT("cheekPuff"),           EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 26
    { TEXT("cheekSquintLeft"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 27
    { TEXT("cheekSquintRight"),    EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 28
    { TEXT("noseSneerLeft"),       EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 29
    { TEXT("noseSneerRight"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 30
    // 31 — tongueOut: no ARKit name. MetaHuman tongue = Control-Rig curves;
    // mapping DEFERRED per RETARGETING.md §4. Do NOT fold into jawOpen
    // (the "mumbling" artifact) — ignore is the spec-correct behaviour.
    { TEXT("tongueOut"),           EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 31
    // idx 32–51 — Extended-20, ARKit canonical relative order (channels.json)
    { TEXT("browOuterUpLeft"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 32
    { TEXT("browOuterUpRight"),    EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 33
    { TEXT("eyeLookDownLeft"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 34
    { TEXT("eyeLookDownRight"),    EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 35
    { TEXT("eyeLookInLeft"),       EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 36
    { TEXT("eyeLookInRight"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 37
    { TEXT("eyeLookOutLeft"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 38
    { TEXT("eyeLookOutRight"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 39
    { TEXT("eyeLookUpLeft"),       EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 40
    { TEXT("eyeLookUpRight"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 41
    { TEXT("eyeSquintLeft"),       EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 42
    { TEXT("eyeSquintRight"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 43
    { TEXT("mouthDimpleLeft"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 44
    { TEXT("mouthDimpleRight"),    EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 45
    { TEXT("mouthLeft"),           EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 46
    { TEXT("mouthRight"),          EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 47
    { TEXT("mouthRollLower"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 48
    { TEXT("mouthRollUpper"),      EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 49
    { TEXT("mouthShrugLower"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 50
    { TEXT("mouthShrugUpper"),     EGorgonRetargetAction::Direct, nullptr, nullptr, 0.0f, 0.0f }, // 51
    // idx 52–71 — Premium-20. Composition weights are ADVISORY client tuning
    // (channels.json carries no weights): the two-term brow compound gets
    // 0.5/0.5 to avoid double-strength; single-term eyeWide gets 1.0.
    { TEXT("eyeUpperLidRaiseLeft"),  EGorgonRetargetAction::Composition, TEXT("eyeWideLeft"),    nullptr,             1.0f, 0.0f }, // 52 → eyeWideLeft
    { TEXT("eyeUpperLidRaiseRight"), EGorgonRetargetAction::Composition, TEXT("eyeWideRight"),   nullptr,             1.0f, 0.0f }, // 53 → eyeWideRight
    { TEXT("eyeLowerLidLowerLeft"),  EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 54 genuine_gap (low)
    { TEXT("eyeLowerLidLowerRight"), EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 55 genuine_gap (low)
    { TEXT("browRaiseLeft"),         EGorgonRetargetAction::Composition, TEXT("browInnerUp"),    TEXT("browOuterUpLeft"),  0.5f, 0.5f }, // 56
    { TEXT("browRaiseRight"),        EGorgonRetargetAction::Composition, TEXT("browInnerUp"),    TEXT("browOuterUpRight"), 0.5f, 0.5f }, // 57
    { TEXT("noseDilationLeft"),      EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 58 genuine_gap (low)
    { TEXT("noseDilationRight"),     EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 59 genuine_gap (low)
    { TEXT("cheekSuckLeft"),         EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 60 genuine_gap (low)
    { TEXT("cheekSuckRight"),        EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 61 genuine_gap (low)
    // 62–63, 66–69 — redundant_with_arkit: clients MAY ignore (channels.json
    // recommendation); cheekSquint*/mouthPress*/mouthClose already drive the
    // same morphs — writing both would double-drive.
    { TEXT("cheekRaiserLowerLeft"),  EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 62 ≡ cheekSquintLeft
    { TEXT("cheekRaiserLowerRight"), EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 63 ≡ cheekSquintRight
    // 64–65 — genuine gaps, speech-relevant, but MetaHuman exposes no morph
    // for independent chin control; Control-Rig curve mapping DEFERRED.
    { TEXT("jawChinRaise"),          EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 64 deferred (Control Rig)
    { TEXT("jawChinLower"),          EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 65 deferred (Control Rig)
    { TEXT("mouthTightLeft"),        EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 66 ≡ mouthPressLeft
    { TEXT("mouthTightRight"),       EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 67 ≡ mouthPressRight
    { TEXT("mouthLipsTogether"),     EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 68 ≡ mouthClose = 1.0
    { TEXT("mouthLipsPart"),         EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 69 ≡ mouthClose = 0.0 @ jawOpen 0
    { TEXT("tongueRaise"),           EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 70 tongue, deferred
    { TEXT("tongueLower"),           EGorgonRetargetAction::Ignore, nullptr, nullptr, 0.0f, 0.0f }, // 71 tongue, deferred
};
// clang-format on

UMetaHumanRetargetComponent::UMetaHumanRetargetComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
    PrimaryComponentTick.TickGroup = TG_PrePhysics;
}

void UMetaHumanRetargetComponent::BeginPlay()
{
    Super::BeginPlay();
    if (TargetMesh != nullptr)
    {
        InitializeMapping();
    }
}

double UMetaHumanRetargetComponent::NowMs() const
{
    return static_cast<double>(FPlatformTime::Seconds()) * 1000.0;
}

void UMetaHumanRetargetComponent::SetTargetMesh(USkeletalMeshComponent* InMesh)
{
    TargetMesh = InMesh;
    InitializeMapping();
}

void UMetaHumanRetargetComponent::WarnMissingMorph(const FName& Name)
{
    bool bAlready = false;
    WarnedMissingMorphs.Add(Name, &bAlready);
    if (!bAlready)
    {
        UE_LOG(LogTemp, Warning,
               TEXT("Gorgon retarget: morph target '%s' not found on the mesh — "
                    "channel ignored (client MUST-ignore rule, PROTOCOL §3)"), *Name.ToString());
    }
}

void UMetaHumanRetargetComponent::InitializeMapping()
{
    MorphNames.Init(NAME_None, FC_NUM_CHANNELS);
    CompTargets.SetNum(FC_NUM_CHANNELS);
    Oef.SetNum(FC_NUM_CHANNELS);
    MappedChannels = 0;

    const USkeletalMeshComponent* Mesh = TargetMesh;
    const USkeletalMesh* Skel = (Mesh != nullptr) ? Mesh->SkeletalMesh.Get() : nullptr;
    if (Skel == nullptr)
    {
        UE_LOG(LogTemp, Warning, TEXT("Gorgon retarget: no target mesh — nothing mapped yet"));
        return;
    }

    for (int32 i = 0; i < FC_NUM_CHANNELS; ++i)
    {
        const FChannelRule& Rule = ChannelRules[i];
        if (Rule.Action == EGorgonRetargetAction::Ignore)
        {
            continue;                              // NAME_None + no targets
        }
        if (Rule.Action == EGorgonRetargetAction::Direct)
        {
            const FName Target(Rule.Name);
            if (Skel->GetMorphTargetIndex(Target) != INDEX_NONE)
            {
                MorphNames[i] = Target;
                ++MappedChannels;
            }
            else
            {
                WarnMissingMorph(Target);
            }
        }
        else // Composition
        {
            if (Rule.CompA != nullptr)
            {
                const FName A(Rule.CompA);
                if (Skel->GetMorphTargetIndex(A) != INDEX_NONE)
                {
                    CompTargets[i].Add(A);
                    ++MappedChannels;
                }
                else
                {
                    WarnMissingMorph(A);
                }
            }
            if (Rule.CompB != nullptr)
            {
                const FName Bn(Rule.CompB);
                if (Skel->GetMorphTargetIndex(Bn) != INDEX_NONE)
                {
                    CompTargets[i].Add(Bn);
                    ++MappedChannels;
                }
                else
                {
                    WarnMissingMorph(Bn);
                }
            }
        }
    }

    UE_LOG(LogTemp, Log, TEXT("Gorgon retarget: %d channels mapped onto morph targets"), MappedChannels);
}

void UMetaHumanRetargetComponent::PrepareFadeIn()
{
    bFadeInPending = true;                         // §6.3 (applied in PlayNext)
}

void UMetaHumanRetargetComponent::ResetFilters()
{
    ResetFiltersInternal(false);
}

void UMetaHumanRetargetComponent::ResetFiltersInternal(bool bKeepLastPose)
{
    for (FOefState& S : Oef)
    {
        S = FOefState();
    }
    bWasSpeaking = false;
    CrossfadeStartMs = -1.0;
    CrossfadeFrom.Reset();
    FadeInStartMs = -1.0;
    FadeInFrom.Reset();
    NextSeq = -1;
    LastPlayMs = -1.0;
    LastRenderMs = 0.0;
    PendingSubFrames.Reset();
    LastWritten.Reset();
    if (!bKeepLastPose)
    {
        LastRenderedPose.Reset();
    }
}

void UMetaHumanRetargetComponent::ApplyFrame(const FFaceContractFrame& Frame)
{
    if (Frame.BsCount <= 0 || Frame.ChunkSize <= 0 ||
        Frame.Payload.Num() != Frame.BsCount * Frame.ChunkSize)
    {
        UE_LOG(LogTemp, Warning, TEXT("Gorgon: malformed frame rejected (should be impossible post-decode)"));
        return;
    }

    // §6.2 client policy on seq gaps / session epochs
    const int64 Seq = Frame.Seq;
    if (NextSeq >= 0)
    {
        if (Seq < NextSeq)
        {
            UE_LOG(LogTemp, Verbose, TEXT("Gorgon: stale/duplicate seq %lld (expected >= %lld) — dropped"),
                   Seq, NextSeq);
            return;
        }
        if (Seq == 0 && NextSeq > 1)
        {
            // Reconnect: server restarts seq at 0 with a fresh t_ns epoch
            // (§6.3). Reset filters, keep the last pose, fade in.
            UE_LOG(LogTemp, Log, TEXT("Gorgon: new session epoch (seq reset) — fade-in armed"));
            ResetFiltersInternal(true);
            bFadeInPending = true;
        }
        else if (Seq > NextSeq)
        {
            // TCP is ordered, so a gap here means transport loss (§6.2).
            // MVP policy: resume at seq, log — interpolation deferred.
            UE_LOG(LogTemp, Warning, TEXT("Gorgon: seq gap %lld → %lld (§6.2) — resuming, interpolation deferred"),
                   NextSeq, Seq);
        }
    }
    NextSeq = Seq + 1;

    const int32 N = Frame.BsCount;

    // Enqueue all sub-frames of the chunk; TickComponent plays them out at
    // the stream fps (§4 pacing). Clamp non-finite values defensively —
    // the canon decode() passes NaN through, morph targets must not get it.
    for (int32 f = 0; f < Frame.ChunkSize; ++f)
    {
        FPendingSubFrame SF;
        SF.bSpeaking = Frame.IsSpeaking();
        SF.Values.SetNumUninitialized(N);
        for (int32 c = 0; c < N; ++c)
        {
            float V = Frame.Payload[f * N + c];
            if (!FMath::IsFinite(V))
            {
                V = 0.0f;
            }
            SF.Values[c] = FMath::Clamp(V, 0.0f, 1.0f);
        }
        PendingSubFrames.Add(MoveTemp(SF));
    }
    CurrentFps = FMath::Max(Frame.Fps, 1);
}

void UMetaHumanRetargetComponent::TickComponent(float DeltaTime, ELevelTick TickType,
                                                FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

    if (PendingSubFrames.Num() == 0)
    {
        return;
    }

    const double Now = NowMs();
    const double FrameMs = 1000.0 / static_cast<double>(FMath::Max(CurrentFps, 1));

    if (LastPlayMs < 0.0)
    {
        LastPlayMs = Now;
        PlayNext(Now);
        return;
    }

    int32 CatchUp = 3;                             // burst guard after a stall
    while (PendingSubFrames.Num() > 0 && CatchUp-- > 0)
    {
        if (Now - LastPlayMs + 1e-6 < FrameMs)
        {
            break;                                 // not due yet
        }
        LastPlayMs += FrameMs;
        if (Now - LastPlayMs > 4.0 * FrameMs)
        {
            LastPlayMs = Now;                      // stalled long → resync
        }
        PlayNext(Now);
    }

    // Transport stall safety valve: keep at most ~2 s of sub-frames.
    const int32 MaxQueue = FMath::Max(CurrentFps, 1) * 2;
    while (PendingSubFrames.Num() > MaxQueue)
    {
        PendingSubFrames.RemoveAt(0);
        UE_LOG(LogTemp, Warning, TEXT("Gorgon: render queue overflow — oldest sub-frame dropped"));
    }
}

void UMetaHumanRetargetComponent::PlayNext(double NowMs)
{
    USkeletalMeshComponent* Mesh = TargetMesh;
    if (Mesh == nullptr || Mesh->SkeletalMesh == nullptr || PendingSubFrames.Num() == 0)
    {
        return;
    }
    if (MorphNames.Num() != FC_NUM_CHANNELS)
    {
        InitializeMapping();
    }

    const FPendingSubFrame SF = PendingSubFrames[0];
    PendingSubFrames.RemoveAt(0);

    const int32 N = SF.Values.Num();
    if (LastRenderedPose.Num() != N)
    {
        LastRenderedPose.Init(0.0f, N);
    }
    if (Oef.Num() != FC_NUM_CHANNELS)
    {
        Oef.SetNum(FC_NUM_CHANNELS);
    }

    // §6.3 fade-in: blend from the last rendered pose (armed on reconnect)
    if (bFadeInPending)
    {
        bFadeInPending = false;
        if (FadeInMs > 0.0f && LastRenderedPose.Num() == N)
        {
            FadeInFrom = LastRenderedPose;
            FadeInStartMs = NowMs;
        }
    }

    // OEF dt: real elapsed render time, clamped to sane bounds
    float Dt = (LastRenderMs > 0.0)
        ? static_cast<float>((NowMs - LastRenderMs) / 1000.0)
        : (CurrentFps > 0 ? 1.0f / static_cast<float>(CurrentFps) : 1.0f / 30.0f);
    Dt = FMath::Clamp(Dt, 0.001f, 0.2f);
    LastRenderMs = NowMs;

    // §7.2 crossfade (MUST) — BEFORE the One Euro Filter (§7.1/RETARGETING §5)
    TArray<float> Blended;
    ProcessCrossfade(SF.Values, SF.bSpeaking, NowMs, Blended);

    if (FadeInStartMs >= 0.0)
    {
        const double P = (NowMs - FadeInStartMs) / static_cast<double>(FadeInMs);
        if (P >= 1.0)
        {
            FadeInStartMs = -1.0;
            FadeInFrom.Reset();
        }
        else
        {
            const float W = static_cast<float>(P);
            for (int32 i = 0; i < N; ++i)
            {
                Blended[i] = FadeInFrom[i] * (1.0f - W) + Blended[i] * W;
            }
        }
    }

    // §7.1 OEF + rig accumulation for this sub-frame
    TMap<FName, float> Accum;
    const int32 Channels = FMath::Min(N, FC_NUM_CHANNELS);
    for (int32 c = 0; c < Channels; ++c)
    {
        const float Filtered = ApplyOneEuro(c, Blended[c], Dt, !SF.bSpeaking);
        const FChannelRule& Rule = ChannelRules[c];
        if (Rule.Action == EGorgonRetargetAction::Direct)
        {
            if (MorphNames[c] != NAME_None)
            {
                Accum.FindOrAdd(MorphNames[c]) += Filtered;
            }
        }
        else if (Rule.Action == EGorgonRetargetAction::Composition)
        {
            const TArray<FName>& Targets = CompTargets[c];
            if (Targets.Num() > 0)
            {
                Accum.FindOrAdd(Targets[0]) += Filtered * Rule.CompAWeight;
            }
            if (Targets.Num() > 1)
            {
                Accum.FindOrAdd(Targets[1]) += Filtered * Rule.CompBWeight;
            }
        }
    }

    // Write the pose; zero morphs that dropped out of this pass so stale
    // values cannot linger (partial levels, mapping changes).
    TSet<FName> Written;
    for (const TPair<FName, float>& It : Accum)
    {
        const float V = FMath::Clamp(It.Value, 0.0f, 1.0f);
        Mesh->SetMorphTarget(It.Key, V);
        Written.Add(It.Key);
    }
    for (const TPair<FName, float>& It : LastWritten)
    {
        if (!Written.Contains(It.Key))
        {
            Mesh->SetMorphTarget(It.Key, 0.0f);
        }
    }
    LastWritten.Reset();
    for (const TPair<FName, float>& It : Accum)
    {
        LastWritten.Add(It.Key, FMath::Clamp(It.Value, 0.0f, 1.0f));
    }
}

void UMetaHumanRetargetComponent::ProcessCrossfade(const TArray<float>& Values,
                                                   bool bSpeaking, double NowMs,
                                                   TArray<float>& Out)
{
    const int32 N = Values.Num();

    // Abort-on-resume: speech resumed mid-crossfade → no blending into idle
    // (§7.2; pinned by verify_behavior.mjs Test 8).
    if (CrossfadeStartMs >= 0.0 && bSpeaking)
    {
        CrossfadeStartMs = -1.0;
        CrossfadeFrom.Reset();
        bWasSpeaking = true;
        LastRenderedPose = Values;
        Out = Values;
        return;
    }

    // SPEAKING → IDLE transition: blend starts from the LAST SPEAKING pose,
    // not the first idle frame (§7.2; pinned by verify_behavior.mjs Test 7).
    if (bWasSpeaking && !bSpeaking && CrossfadeStartMs < 0.0)
    {
        CrossfadeStartMs = NowMs;
        CrossfadeFrom = (LastRenderedPose.Num() == N) ? LastRenderedPose : Values;
    }
    bWasSpeaking = bSpeaking;

    if (CrossfadeStartMs < 0.0)
    {
        LastRenderedPose = Values;
        Out = Values;
        return;
    }

    const double Progress = FMath::Min(1.0, (NowMs - CrossfadeStartMs) / static_cast<double>(CrossfadeMs));
    if (Progress >= 1.0)
    {
        CrossfadeStartMs = -1.0;
        CrossfadeFrom.Reset();
        LastRenderedPose = Values;
        Out = Values;
        return;
    }

    Out.SetNum(N);
    const float P = static_cast<float>(Progress);
    for (int32 i = 0; i < N; ++i)
    {
        Out[i] = CrossfadeFrom[i] * (1.0f - P) + Values[i] * P;
    }
    LastRenderedPose = Out;
}

UMetaHumanRetargetComponent::EProfile
UMetaHumanRetargetComponent::ProfileFor(int32 Channel, bool bIdleChunk) const
{
    if (Channel == 0 || Channel == 1)
    {
        return EProfile::Blink;                    // eyeBlinkLeft/Right
    }
    // jaw* (7–10), mouth* (11–25, 44–51), tongueOut (31)
    const bool bArticulation =
        (Channel >= 7 && Channel <= 25) || Channel == 31 ||
        (Channel >= 44 && Channel <= 51);
    if (bArticulation)
    {
        // §7.1: in FLAG_IDLE chunks the articulation profile degrades to
        // static; in FLAG_SPEAKING it is mandatory.
        return bIdleChunk ? EProfile::Static : EProfile::Articulation;
    }
    return EProfile::Static;
}

float UMetaHumanRetargetComponent::ApplyOneEuro(int32 Channel, float Value,
                                                float DtSeconds, bool bIdleChunk)
{
    if (Channel < 0 || Channel >= Oef.Num())
    {
        return Value;
    }
    FOefState& S = Oef[Channel];
    if (!S.bInit)
    {
        S.XPrev = Value;
        S.DxPrev = 0.0;
        S.bInit = true;
        return Value;
    }
    const float Dt = FMath::Max(DtSeconds, 0.001f);

    double FcMin = StaticFcMin;
    double Beta = StaticBeta;
    switch (ProfileFor(Channel, bIdleChunk))
    {
    case EProfile::Blink:
        FcMin = BlinkFcMin;
        Beta = BlinkBeta;
        break;
    case EProfile::Articulation:
        FcMin = ArticulationFcMin;
        Beta = ArticulationBeta;
        break;
    case EProfile::Static:
        break;
    }

    // Standard One Euro Filter (d_cutoff = 1.0 Hz)
    const double Dx = (static_cast<double>(Value) - S.XPrev) / Dt;
    const double TauD = 1.0 / (2.0 * FMath::PI * 1.0);
    const double AlphaD = 1.0 / (1.0 + TauD / Dt);
    const double DxHat = AlphaD * Dx + (1.0 - AlphaD) * S.DxPrev;
    const double Cutoff = FcMin + Beta * FMath::Abs(DxHat);
    const double Tau = 1.0 / (2.0 * FMath::PI * Cutoff);
    const double Alpha = 1.0 / (1.0 + Tau / Dt);
    const double XHat = Alpha * static_cast<double>(Value) + (1.0 - Alpha) * S.XPrev;

    S.DxPrev = DxHat;
    S.XPrev = XHat;
    return static_cast<float>(XHat);
}
