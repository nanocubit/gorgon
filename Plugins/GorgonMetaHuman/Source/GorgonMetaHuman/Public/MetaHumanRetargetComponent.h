// MetaHumanRetargetComponent.h — FaceContract → MetaHuman (ARKit-morph) rig.
//
// Pipeline order (PROTOCOL.md §7 + RETARGETING.md §5):
//   unpack sub-frame → §7.2 crossfade (MUST, 200 ms, last-speech capture,
//   abort-on-resume) → §6.3 reconnect fade-in → §7.1 One Euro Filter
//   (per-channel profiles, flag-aware) → rig mapping (channels.json).
//
// Sub-frames are PLAYED OUT at the stream fps by TickComponent (paced by
// real elapsed time, per §4: t_ns is ground truth, dur_us is a hint), not
// dumped at once — a chunk is 4 sub-frames @30 fps.
//
// Mapping policy (channels.json blob 25705b7c…, RETARGETING.md):
//   idx 0–30, 32–51   direct_by_name (ARKit morph targets)
//   idx 52,53,56,57   composition_alternative — additive onto the ARKit
//                     compound morphs (eyeWide*, browInnerUp+browOuterUp*)
//   idx 31            tongueOut — MetaHuman tongue = Control-Rig curves,
//                     DEFERRED (RETARGETING §4); NEVER folded into jawOpen
//   idx 54,55,58–61   genuine_gap low-priority — ignored (no morph exists)
//   idx 64,65         jawChinRaise/Lower — genuine gaps, speech-relevant,
//                     but need Control-Rig curves; DEFERRED (loud comment,
//                     not a silent drop)
//   idx 62,63,66–69   redundant_with_arkit — ignored (ARKit equivalents
//                     already drive the same morphs; clients MAY ignore)
//   idx 70,71         tongueRaise/Lower — deferred with the tongue block
//
// A client MUST ignore channels absent from its morph dictionary
// (PROTOCOL §3) — a Level-2 stream on a Level-1 rig is partial animation,
// not an error.
#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "FaceContractDecoder.h"
#include "MetaHumanRetargetComponent.generated.h"

class USkeletalMeshComponent;

UENUM(BlueprintType)
enum class EGorgonRetargetAction : uint8
{
    Direct,       // 1:1 morph target by the same ARKit name (idx 0–30, 32–51)
    Composition,  // additive onto ARKit compound morphs (idx 52, 53, 56, 57)
    Ignore        // genuine gap / redundant / tongue — see header comments
};

/** One rendered sub-frame pending in the playout queue. */
struct FPendingSubFrame
{
    TArray<float> Values;
    bool bSpeaking = false;
};

UCLASS(ClassGroup=(Gorgon), meta=(BlueprintSpawnableComponent))
class GORGONMETAHUMAN_API UMetaHumanRetargetComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    UMetaHumanRetargetComponent();

    virtual void BeginPlay() override;
    virtual void TickComponent(float DeltaTime, ELevelTick TickType,
                               FActorComponentTickFunction* ThisTickFunction) override;

    UFUNCTION(BlueprintCallable, Category="Gorgon")
    void SetTargetMesh(USkeletalMeshComponent* InMesh);

    /** Enqueue one decoded chunk (ChunkSize sub-frames @ Fps). Rendering is
     *  paced by TickComponent at the stream fps (§4 pacing). */
    UFUNCTION(BlueprintCallable, Category="Gorgon")
    void ApplyFrame(const FFaceContractFrame& Frame);

    /** §6.3: arm a ~200 ms fade-in from the last rendered pose (reconnect). */
    UFUNCTION(BlueprintCallable, Category="Gorgon")
    void PrepareFadeIn();

    /** Clear all filters/state (keeps the last pose for fade-in). */
    UFUNCTION(BlueprintCallable, Category="Gorgon")
    void ResetFilters();

    UFUNCTION(BlueprintPure, Category="Gorgon")
    int32 GetMappedChannelCount() const { return MappedChannels; }

    UFUNCTION(BlueprintPure, Category="Gorgon")
    int32 GetPendingSubFrameCount() const { return PendingSubFrames.Num(); }

    UFUNCTION(BlueprintPure, Category="Gorgon")
    USkeletalMeshComponent* GetTargetMesh() const { return TargetMesh; }

private:
    enum : int32 { FC_NUM_CHANNELS = 72 };

    /** §7.1 profile buckets. */
    enum class EProfile : uint8 { Static, Blink, Articulation };

    /** Per-channel rule — mirrors channels.json (blob 25705b7c…). The full
     *  table lives in the .cpp; keep it in sync with channels.json. */
    struct FChannelRule
    {
        const TCHAR* Name;
        EGorgonRetargetAction Action;
        const TCHAR* CompA;          // composition targets (null otherwise)
        const TCHAR* CompB;
        float CompAWeight;           // advisory client tuning (see cpp)
        float CompBWeight;
    };
    static const FChannelRule ChannelRules[FC_NUM_CHANNELS];

    struct FOefState
    {
        double XPrev = 0.0;
        double DxPrev = 0.0;
        bool bInit = false;
    };

    UPROPERTY()
    USkeletalMeshComponent* TargetMesh = nullptr;

    /** §7.2 MUST: 200 ms ±50. */
    UPROPERTY(EditAnywhere, Category="Gorgon|Smoothing", meta=(ClampMin="50", ClampMax="500"))
    float CrossfadeMs = 200.0f;

    /** §6.3 reconnect fade-in. */
    UPROPERTY(EditAnywhere, Category="Gorgon|Smoothing", meta=(ClampMin="50", ClampMax="500"))
    float FadeInMs = 200.0f;

    // §7.1 profiles (verify_behavior.mjs-measured)
    UPROPERTY(EditAnywhere, Category="Gorgon|Smoothing") float BlinkFcMin = 5.0f;
    UPROPERTY(EditAnywhere, Category="Gorgon|Smoothing") float BlinkBeta = 0.10f;
    UPROPERTY(EditAnywhere, Category="Gorgon|Smoothing") float ArticulationFcMin = 1.5f;
    UPROPERTY(EditAnywhere, Category="Gorgon|Smoothing") float ArticulationBeta = 0.03f;
    UPROPERTY(EditAnywhere, Category="Gorgon|Smoothing") float StaticFcMin = 1.0f;
    UPROPERTY(EditAnywhere, Category="Gorgon|Smoothing") float StaticBeta = 0.007f;

    // mapping (per FC channel)
    TArray<FName> MorphNames;             // NAME_None = not mapped / absent
    TArray<TArray<FName>> CompTargets;    // resolved composition targets
    TArray<FOefState> Oef;
    TSet<FName> WarnedMissingMorphs;
    int32 MappedChannels = 0;

    // playout + render state
    TArray<FPendingSubFrame> PendingSubFrames;
    int32 CurrentFps = 30;
    double LastPlayMs = -1.0;
    double LastRenderMs = 0.0;
    TArray<float> LastRenderedPose;
    TMap<FName, float> LastWritten;

    // §6.2 client seq policy
    int64 NextSeq = -1;

    // §7.2 crossfade (port of facecontract_v141.mjs CrossfadeFilter)
    bool bWasSpeaking = false;
    double CrossfadeStartMs = -1.0;
    TArray<float> CrossfadeFrom;

    // §6.3 fade-in
    bool bFadeInPending = false;
    double FadeInStartMs = -1.0;
    TArray<float> FadeInFrom;

    void InitializeMapping();
    void PlayNext(double NowMs);
    void ProcessCrossfade(const TArray<float>& Values, bool bSpeaking,
                          double NowMs, TArray<float>& Out);
    float ApplyOneEuro(int32 Channel, float Value, float DtSeconds, bool bIdleChunk);
    EProfile ProfileFor(int32 Channel, bool bIdleChunk) const;
    void ResetFiltersInternal(bool bKeepLastPose);
    void WarnMissingMorph(const FName& Name);
    double NowMs() const;
};
