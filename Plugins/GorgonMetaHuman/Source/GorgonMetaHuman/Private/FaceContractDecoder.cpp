// FaceContractDecoder.cpp — canon mirror, see header for the validation order.
#include "FaceContractDecoder.h"

namespace
{
    struct FCrc32Table
    {
        uint32 Table[256];
        FCrc32Table()
        {
            for (uint32 n = 0; n < 256; ++n)
            {
                uint32 c = n;
                for (int32 k = 0; k < 8; ++k)
                {
                    c = ((c & 1u) != 0u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
                }
                Table[n] = c;
            }
        }
    };
    const FCrc32Table GCrc32Table;

    // Explicit little-endian readers — no reinterpret_cast, no alignment or
    // endianness assumptions (mirrors struct '<...' / DataView LE semantics).
    uint16 ReadU16(const uint8* P)
    {
        return static_cast<uint16>(P[0] | (P[1] << 8));
    }
    uint32 ReadU32(const uint8* P)
    {
        return static_cast<uint32>(P[0]) |
               (static_cast<uint32>(P[1]) << 8) |
               (static_cast<uint32>(P[2]) << 16) |
               (static_cast<uint32>(P[3]) << 24);
    }
    uint64 ReadU64(const uint8* P)
    {
        uint64 V = 0;
        for (int32 i = 7; i >= 0; --i)
        {
            V = (V << 8) | static_cast<uint64>(P[i]);
        }
        return V;
    }
}

uint32 FFaceContractDecoder::ComputeCrc(const uint8* Data, int32 Length)
{
    uint32 Crc = 0xFFFFFFFFu;
    for (int32 i = 0; i < Length; ++i)
    {
        Crc = GCrc32Table.Table[(Crc ^ Data[i]) & 0xFFu] ^ (Crc >> 8);
    }
    return Crc ^ 0xFFFFFFFFu;
}

uint32 FFaceContractDecoder::ComputeWholeFrameResidue(const TArray<uint8>& Data)
{
    return ComputeCrc(Data.GetData(), Data.Num());
}

bool FFaceContractDecoder::Decode(const TArray<uint8>& Data,
                                  FFaceContractFrame& OutFrame, FString* OutError)
{
    auto Fail = [&OutError](const FString& Msg) -> bool
    {
        if (OutError != nullptr)
        {
            *OutError = Msg;
        }
        UE_LOG(LogTemp, Warning, TEXT("FaceContract: %s"), *Msg);
        return false;
    };

    // 1) length
    if (Data.Num() < FRAME_OVERHEAD)
    {
        return Fail(FString::Printf(TEXT("frame too short: %d bytes"), Data.Num()));
    }

    const uint8* B = Data.GetData();

    // 2) magic (byte-compare — endian-proof)
    if (!(B[0] == 'G' && B[1] == 'R' && B[2] == 'G' && B[3] == '1'))
    {
        return Fail(TEXT("bad magic"));
    }

    // 3) version — a v1.4.1 client rejects every other wire version
    const uint8 VMaj = B[4];
    const uint8 VMin = B[5];
    if (VMaj != VERSION_MAJOR || VMin != VERSION_MINOR)
    {
        return Fail(FString::Printf(TEXT("unsupported version: %u.%u"), VMaj, VMin));
    }

    const uint16 Fps = ReadU16(B + 8);
    const uint16 ChunkSize = ReadU16(B + 10);

    // 4/5) sanity
    if (Fps < 1)
    {
        return Fail(FString::Printf(TEXT("fps must be >= 1: %u"), static_cast<uint32>(Fps)));
    }
    if (ChunkSize < 1)
    {
        return Fail(FString::Printf(TEXT("chunk_size must be >= 1: %u"), static_cast<uint32>(ChunkSize)));
    }

    const uint32 PayloadLen = ReadU32(B + 20);

    // 6) payload_len cross-check BEFORE the CRC (§5.2): a corrupted length or
    //    truncated frame produces this specific error even with a broken CRC.
    if (PayloadLen != static_cast<uint32>(Data.Num() - FRAME_OVERHEAD))
    {
        return Fail(FString::Printf(TEXT("payload_len mismatch: header=%u actual=%d"),
                                    PayloadLen, Data.Num() - FRAME_OVERHEAD));
    }
    if (PayloadLen % 4u != 0u)
    {
        return Fail(FString::Printf(TEXT("payload_len is not a multiple of 4: %u"), PayloadLen));
    }

    // 7) CRC (Check A) + §5.1 residue diagnostic on failure
    const uint32 StoredCrc = ReadU32(B + HEADER_SIZE + PayloadLen);
    const uint32 ComputedCrc = ComputeCrc(B, HEADER_SIZE + static_cast<int32>(PayloadLen));
    if (StoredCrc != ComputedCrc)
    {
        const uint32 Residue = ComputeWholeFrameResidue(Data);
        UE_LOG(LogTemp, Warning,
               TEXT("FaceContract: residue diagnostic 0x%08X — valid LE frames residue "
                    "to 0x2144DF1C; a message-dependent value here points at "
                    "big-endian CRC storage (PROTOCOL §5.1)"), Residue);
        return Fail(FString::Printf(TEXT("CRC mismatch: stored=0x%08X computed=0x%08X"),
                                    StoredCrc, ComputedCrc));
    }

    // 8) flags — the canon decode() rejects ANY bit outside 0x03: bit 2 is
    //    reserved for the v1.5 head_pose block, bits 3–7 must be 0. (Stricter
    //    than §2's receiver MUST-ignore prose — matches facecontract_v141.py.)
    const uint8 Flags = B[6];
    if ((Flags & static_cast<uint8>(~KNOWN_FLAGS)) != 0u)
    {
        return Fail(FString::Printf(TEXT("unknown flag bits: 0x%02X"), Flags));
    }
    const int32 Ones = (((Flags & FLAG_IDLE) != 0u) ? 1 : 0) +
                       (((Flags & FLAG_SPEAKING) != 0u) ? 1 : 0);
    if (Ones != 1)
    {
        return Fail(TEXT("I3 violation: flags must be one-hot (IDLE or SPEAKING)"));
    }

    // 9/10) chunk multiples + bs_count cross-check (I6)
    const uint8 BsCount = B[7];
    const uint32 NumFloats = PayloadLen / 4u;
    if (NumFloats % static_cast<uint32>(ChunkSize) != 0u)
    {
        return Fail(TEXT("payload not a multiple of chunk_size"));
    }
    if (NumFloats / static_cast<uint32>(ChunkSize) != static_cast<uint32>(BsCount))
    {
        return Fail(FString::Printf(TEXT("bs_count mismatch: header=%u derived=%u"),
                                    static_cast<uint32>(BsCount),
                                    NumFloats / static_cast<uint32>(ChunkSize)));
    }

    OutFrame = FFaceContractFrame();
    OutFrame.Seq = static_cast<int64>(ReadU32(B + 12));
    OutFrame.Tns = static_cast<int64>(ReadU64(B + 24));
    OutFrame.DurUs = static_cast<int32>(ReadU32(B + 16));
    OutFrame.Fps = static_cast<int32>(Fps);
    OutFrame.ChunkSize = static_cast<int32>(ChunkSize);
    OutFrame.BsCount = static_cast<int32>(BsCount);
    OutFrame.Flags = Flags;

    OutFrame.Payload.SetNumUninitialized(static_cast<int32>(NumFloats));
    FMemory::Memcpy(OutFrame.Payload.GetData(), B + HEADER_SIZE,
                    static_cast<int32>(PayloadLen));
    return true;
}
