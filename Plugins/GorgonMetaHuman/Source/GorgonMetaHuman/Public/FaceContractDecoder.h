// FaceContractDecoder.h — FaceContract v1.4.1 wire decoder for UE5.
//
// Byte-for-byte mirror of facecontract_v141.py decode() (validation order
// included) and facecontract_v141.mjs decodeFrame(). Any frame accepted by
// the Python canon decodes identically here; any frame rejected there is
// rejected here with the same precedence:
//
//   length → magic → version → fps → chunk_size → payload_len (BEFORE the
//   CRC — §5.2 defence-in-depth) → CRC (+ §5.1 residue diagnostic) →
//   unknown flags → one-hot (I3) → chunk multiples → bs_count cross-check.
//
// v1.4.1 has NO head pose: flag bit 2 is reserved for v1.5 (PROTOCOL §10,
// minor + wire bump). A frame carrying it is rejected as an unknown flag
// bit, exactly like facecontract_v141.py (KNOWN_FLAGS = IDLE|SPEAKING).
//
// Provenance: PROTOCOL.md §1–§5 (LOCKED 2026-11-26), repo nanocubit/gorgon.
#pragma once

#include "CoreMinimal.h"
#include "FaceContractDecoder.generated.h"

/** Decoded chunk frame (BP-safe view of the wire struct). The payload is
 *  row-major [frame][bs]: bs_count * chunk_size float32 LE values; each WS
 *  binary message carries exactly one frame. */
USTRUCT(BlueprintType)
struct FFaceContractFrame
{
    GENERATED_BODY()

    /** u32 on the wire; widened to int64 for BP visibility. */
    UPROPERTY(BlueprintReadOnly, Category="Gorgon")
    int64 Seq = 0;

    /** u64 t_ns on the wire — ground truth for pacing (PROTOCOL §4).
     *  Pace by t_ns deltas, NEVER by dur_us (advisory floor). */
    UPROPERTY(BlueprintReadOnly, Category="Gorgon")
    int64 Tns = 0;

    /** Advisory pre-allocation hint only (§4.2). */
    UPROPERTY(BlueprintReadOnly, Category="Gorgon")
    int32 DurUs = 0;

    UPROPERTY(BlueprintReadOnly, Category="Gorgon")
    int32 Fps = 0;

    /** Frames per chunk — the payload holds ChunkSize sub-frames. */
    UPROPERTY(BlueprintReadOnly, Category="Gorgon")
    int32 ChunkSize = 0;

    /** Blendshape count (Level 0/1/2 = 32/52/72; test levels 1,2,4,8). */
    UPROPERTY(BlueprintReadOnly, Category="Gorgon")
    int32 BsCount = 0;

    /** FLAG_IDLE=0x01 / FLAG_SPEAKING=0x02 — one-hot (I3). */
    UPROPERTY(BlueprintReadOnly, Category="Gorgon")
    uint8 Flags = 0;

    /** bs_count * chunk_size float values, row-major [frame][bs]. */
    UPROPERTY(BlueprintReadOnly, Category="Gorgon")
    TArray<float> Payload;

    int32 NumFrames() const { return ChunkSize; }
    UFUNCTION(BlueprintPure, Category="Gorgon")
    bool IsIdle() const { return (Flags & 0x01) != 0; }
    UFUNCTION(BlueprintPure, Category="Gorgon")
    bool IsSpeaking() const { return (Flags & 0x02) != 0; }
};

// Shared delegate types (the payload struct is a USTRUCT, so dynamic
// delegates are legal). Declared here so client and actor headers agree.
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnFrameReceived, const FFaceContractFrame&, Frame);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnTriggerReceived, const FString&, Reaction);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FOnGorgonConnectionStateChanged, bool, bConnected, const FString&, Reason);

class GORGONMETAHUMAN_API FFaceContractDecoder
{
public:
    static constexpr uint32 MAGIC = 0x31475247u;            // "GRG1" little-endian
    static constexpr uint8  VERSION_MAJOR = 0x01;
    static constexpr uint8  VERSION_MINOR = 0x04;
    static constexpr int32  HEADER_SIZE = 32;
    static constexpr int32  CRC_SIZE = 4;
    static constexpr int32  FRAME_OVERHEAD = HEADER_SIZE + CRC_SIZE; // 36
    static constexpr uint8  FLAG_IDLE = 0x01;
    static constexpr uint8  FLAG_SPEAKING = 0x02;
    static constexpr uint8  KNOWN_FLAGS = FLAG_IDLE | FLAG_SPEAKING; // 0x03

    /** §5.1 Check B: crc32(M ‖ LE-crc32(M)) is constant for any valid frame.
     *  A whole-frame residue != 0x2144DF1C on a Check-A failure with a
     *  message-dependent value points at big-endian CRC storage. */
    static constexpr uint32 CRC_RESIDUE = 0x2144DF1Cu;

    /** Decode + fully validate one wire frame. Returns false and fills
     *  OutError with the canon error string on any violation. */
    static bool Decode(const TArray<uint8>& Data, FFaceContractFrame& OutFrame,
                       FString* OutError = nullptr);

    /** IEEE CRC-32 (reflected poly 0xEDB88320, init/xorout 0xFFFFFFFF) —
     *  identical to zlib.crc32 / the Python and JS canons. */
    static uint32 ComputeCrc(const uint8* Data, int32 Length);

    /** §5.1 Check B helper: CRC over the WHOLE frame including its CRC field. */
    static uint32 ComputeWholeFrameResidue(const TArray<uint8>& Data);
};
