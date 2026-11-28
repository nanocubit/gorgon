"""generate_ue5_assets.py — regenerate the UE5 conformance assets from the
Python canon (facecontract_v141.py):

  1. ue5_golden_vectors.json  (this directory) — golden hex vectors +
     expected fields, generated and validated by the canon itself. Frame
     hex is stored as 64-char SEGMENTS (frame_hex_parts) — long single-line
     hex is error-prone to review and transcribe.
  2. Plugins/GorgonMetaHuman/Source/GorgonMetaHuman/Private/
     FaceContractDecoderTests.cpp — UE automation tests with the vector
     tables injected from the JSON. Hex constants are concatenated TEXT()
     literals (2 segments per physical line); the canon verifier joins them
     and byte-checks every frame.

Run from the repo root:
    python test_vectors/generate_ue5_assets.py
Then re-verify the generated .cpp against the canon:
    python test_vectors/verify_ue5_tests.py
"""
import json
import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import facecontract_v141 as fc   # noqa: E402

JSON_PATH = os.path.join(HERE, "ue5_golden_vectors.json")
CPP_PATH = os.path.join(ROOT, "Plugins", "GorgonMetaHuman", "Source",
                        "GorgonMetaHuman", "Private",
                        "FaceContractDecoderTests.cpp")

IDX = fc.NAME_TO_INDEX
SEG = 64                    # hex chars per segment


def hex_parts(hexstr):
    return [hexstr[i:i + SEG] for i in range(0, len(hexstr), SEG)]


def const_name(name, prefix=""):
    camel = "".join(p.capitalize() for p in name.split("_"))
    return f"{prefix}{camel}Hex"


def rows_to_payload(rows):
    return [v for row in rows for v in row]


def make_idle_row(t):
    row = [0.001] * 32
    row[IDX["eyeBlinkLeft"]] = 0.0
    row[IDX["eyeBlinkRight"]] = 0.0
    row[IDX["jawOpen"]] = 0.032 + 0.026 * math.sin(
        2 * math.pi * t / 32 - math.pi / 2)
    row[IDX["mouthClose"]] = 0.010
    return row


def make_speech_row(t):
    row = [0.0] * 32
    row[IDX["jawOpen"]] = 0.30 + 0.10 * math.sin(2 * math.pi * t / 6)
    row[IDX["mouthUpperUpLeft"]] = 0.12
    row[IDX["mouthUpperUpRight"]] = 0.12
    row[IDX["mouthLowerDownLeft"]] = 0.08
    row[IDX["mouthLowerDownRight"]] = 0.08
    return row


def extend_row(row32, bs_count):
    """Extend a Core-32 row to bs_count (52/72) — Extended/Premium tail is
    deterministic filler; only Core-32 channels carry signal here."""
    extra = [0.0] * (bs_count - 32)
    for k in range(len(extra)):
        extra[k] = 0.002 * ((k % 7) + 1) / 7.0
    return list(row32) + extra


def frame_expect(frame):
    d = fc.decode(frame)
    return {
        "seq": d["seq"], "t_ns": d["t_ns"], "dur_us": d["dur_us"],
        "fps": d["fps"], "chunk_size": d["chunk_size"],
        "bs_count": d["bs_count"], "flags": d["flags"],
        "payload_first6": d["payload"][:6],
        "jawOpen": d["payload"][IDX["jawOpen"]],
        "eyeBlinkLeft": d["payload"][IDX["eyeBlinkLeft"]],
        "payload_len": len(d["payload"]),
        "crc": f"{fc.frame_crc(frame):08x}",
        "whole_residue": f"{zlib.crc32(frame) & 0xFFFFFFFF:08x}",
    }


def _fix_crc(raw):
    """Recompute the trailing CRC so a mutated header reaches the flag /
    bs_count checks (canon order: CRC precedes flags)."""
    bad = bytearray(raw)
    n = struct.unpack_from("<I", bad, 20)[0]
    crc = zlib.crc32(bytes(bad[:32 + n])) & 0xFFFFFFFF
    struct.pack_into("<I", bad, 32 + n, crc)
    return bad


def build_vectors():
    dur_us = 133333
    dur_ns = 133333333
    doc = {"magic": "GRG1", "version": "1.4",
           "crc_residue": f"{fc.CRC_RESIDUE:08x}",
           "hex_segment_chars": SEG,
           "positive": [], "negative": [], "stream": {}}

    def entry(name, frame):
        return {"name": name, "frame_hex_parts": hex_parts(frame.hex()),
                "expect": frame_expect(frame)}

    # ── positive: bs32 idle ──
    rows = [make_idle_row(t) for t in range(4)]
    f = fc.encode(seq=0, t_ns=0, dur_us=dur_us, fps=30, chunk_size=4,
                  payload=rows_to_payload(rows), flags=fc.FLAG_IDLE)
    doc["positive"].append(entry("idle_bs32", f))

    # ── positive: bs32 speaking ──
    rows = [make_speech_row(t) for t in range(4)]
    f = fc.encode(seq=1, t_ns=dur_ns, dur_us=dur_us, fps=30, chunk_size=4,
                  payload=rows_to_payload(rows), flags=fc.FLAG_SPEAKING)
    doc["positive"].append(entry("speech_bs32", f))

    # ── positive: bs52 (Extended) ──
    rows = [extend_row(make_speech_row(t), 52) for t in range(4)]
    f = fc.encode(seq=2, t_ns=2 * dur_ns, dur_us=dur_us, fps=30, chunk_size=4,
                  payload=rows_to_payload(rows), flags=fc.FLAG_SPEAKING)
    doc["positive"].append(entry("speech_bs52", f))

    # ── positive: bs72 (Premium) ──
    rows = [extend_row(make_speech_row(t), 72) for t in range(4)]
    f = fc.encode(seq=3, t_ns=3 * dur_ns, dur_us=dur_us, fps=30, chunk_size=4,
                  payload=rows_to_payload(rows), flags=fc.FLAG_SPEAKING)
    doc["positive"].append(entry("speech_bs72", f))

    # ── negatives (canon rejection order) ──
    base = doc["positive"][1]           # speech_bs32 as the mutation source
    raw = bytearray(bytes.fromhex("".join(base["frame_hex_parts"])))

    bad = bytearray(raw); bad[40] ^= 0xFF        # payload bit rot
    doc["negative"].append({"name": "corrupted_crc",
                            "frame_hex_parts": hex_parts(bytes(bad).hex()),
                            "error_prefix": "CRC mismatch"})

    bad = bytearray(raw); bad[0] = ord("X")
    doc["negative"].append({"name": "bad_magic",
                            "frame_hex_parts": hex_parts(bytes(bad).hex()),
                            "error_prefix": "bad magic"})

    bad = bytearray(raw); bad[5] = 5             # minor 4 → 5
    doc["negative"].append({"name": "unsupported_version",
                            "frame_hex_parts": hex_parts(bytes(bad).hex()),
                            "error_prefix": "unsupported version"})

    bad = bytearray(raw); bad[6] |= 0x04         # FLAG_HEAD_POSE (v1.5 resv.)
    doc["negative"].append({"name": "unknown_flag_bit_head_pose",
                            "frame_hex_parts": hex_parts(bytes(_fix_crc(bad)).hex()),
                            "error_prefix": "unknown flag bits"})

    bad = bytearray(raw); bad[6] = 0x00          # one-hot violation
    doc["negative"].append({"name": "zero_flags_i3",
                            "frame_hex_parts": hex_parts(bytes(_fix_crc(bad)).hex()),
                            "error_prefix": "I3 violation"})

    doc["negative"].append({"name": "truncated_payload",
                            "frame_hex_parts": hex_parts(bytes(raw[:-4]).hex()),
                            "error_prefix": "payload_len mismatch"})

    bad = bytearray(raw); bad[7] = 72            # lying bs_count header
    doc["negative"].append({"name": "bs_count_mismatch_i6",
                            "frame_hex_parts": hex_parts(bytes(_fix_crc(bad)).hex()),
                            "error_prefix": "bs_count mismatch"})

    # ── stream: 6 frames seq 0..5, alternating idle/speech ──
    frames = []
    for i in range(6):
        rows = ([make_idle_row(t) for t in range(4)] if i % 2 == 0
                else [make_speech_row(t) for t in range(4)])
        frames.append(fc.encode(
            seq=i, t_ns=i * dur_ns, dur_us=dur_us, fps=30, chunk_size=4,
            payload=rows_to_payload(rows),
            flags=fc.FLAG_IDLE if i % 2 == 0 else fc.FLAG_SPEAKING))
    doc["stream"] = {
        "name": "six_frame_i1_i2", "frame_count": 6,
        "frames_hex_parts": [hex_parts(f.hex()) for f in frames],
        "seqs": [0, 1, 2, 3, 4, 5],
        "t_ns": [i * dur_ns for i in range(6)],
        "dur_ns": dur_ns,
        "flags": [fc.FLAG_IDLE if i % 2 == 0 else fc.FLAG_SPEAKING
                  for i in range(6)],
    }
    return doc


CPP_TEMPLATE = r"""// FaceContractDecoderTests.cpp — UE automation conformance tests for
// FFaceContractDecoder, pinning golden vectors generated from the Python
// canon (facecontract_v141.py) — see test_vectors/ue5_golden_vectors.json.
//
// NOTE: this file is GENERATED by test_vectors/generate_ue5_assets.py
// (repo root); regenerate instead of hand-editing the vector tables (the
// hex payloads are the contract, source of truth: ue5_golden_vectors.json).
//
// Hex frames are stored as named constants of CONCATENATED TEXT() literals
// (2 segments of 64 hex chars per physical line) — no single over-long
// literal lines. test_vectors/verify_ue5_tests.py joins them and byte-checks
// every frame against the canon.
//
// Coverage:
//   Positive   — bs32 idle + bs32/52/72 speech frames: full field check,
//                payload spot values, CRC residue (§5.1 Check B)
//   Negative   — canon rejection order: corrupted CRC, bad magic, wrong
//                version, unknown flag bit 0x04 (head_pose reserved v1.5),
//                zero flags (I3), truncated payload (§5.2 payload_len BEFORE
//                CRC), lying bs_count header (I6)
//   StreamI1I2 — 6-frame stream: seq strict +1, t_ns strict step
//                133333333 ns (PROTOCOL §4)
#include "Misc/AutomationTest.h"
#include "FaceContractDecoder.h"

namespace
{
    struct FGoldenVector
    {
        const TCHAR* Name;
        const TCHAR* Hex;          // whole frame, hex
        int64 ExpectedSeq;
        int64 ExpectedTns;
        int32 ExpectedDurUs;
        int32 ExpectedFps;
        int32 ExpectedChunkSize;
        int32 ExpectedBsCount;
        uint8 ExpectedFlags;
        float ExpectedJawOpen;     // Core-32 index 10 (Extended/Premium too)
        float ExpectedBlinkL;      // Core-32 index 0
    };

    struct FNegativeVector
    {
        const TCHAR* Name;
        const TCHAR* Hex;
        const TCHAR* ErrorPrefix;  // canon error string prefix
    };

    TArray<uint8> HexToBytes(const TCHAR* Hex)
    {
        TArray<uint8> Out;
        auto Nibble = [](TCHAR C) -> uint8
        {
            if (C >= TEXT('0') && C <= TEXT('9')) return (uint8)(C - TEXT('0'));
            if (C >= TEXT('a') && C <= TEXT('f')) return (uint8)(10 + C - TEXT('a'));
            return (uint8)(10 + C - TEXT('A'));
        };
        const int32 Len = FCString::Strlen(Hex);
        Out.Reserve(Len / 2);
        for (int32 i = 0; i + 1 < Len; i += 2)
        {
            Out.Add((uint8)((Nibble(Hex[i]) << 4) | Nibble(Hex[i + 1])));
        }
        return Out;
    }

    bool NearlyEqual(float A, float B)
    {
        return FMath::Abs(A - B) < 1.0e-6f;
    }

    bool NearlyEqual64(int64 A, int64 B)
    {
        return FMath::Abs(static_cast<double>(A) - static_cast<double>(B))
            < 1.0;
    }

__HEX_CONSTANTS__
__VECTOR_DATA__
} // namespace

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FFaceContractDecoder_PositiveVectors,
    "GorgonMetaHuman.FaceContractDecoder.PositiveVectors",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
    EAutomationTestFlags::ServerContext | EAutomationTestFlags::ProductFilter)

bool FFaceContractDecoder_PositiveVectors::RunTest(const FString& Parameters)
{
    for (const FGoldenVector& V : GGoldenVectors)
    {
        TArray<uint8> Bytes = HexToBytes(V.Hex);
        FFaceContractFrame Frame;
        FString Error;
        const bool bOk = FFaceContractDecoder::Decode(Bytes, Frame, &Error);
        TestTrue(FString::Printf(TEXT("%s: decodes"), V.Name), bOk, Error);
        if (!bOk)
        {
            continue;
        }
        TestEqual(FString::Printf(TEXT("%s: seq"), V.Name), Frame.Seq, V.ExpectedSeq);
        TestEqual(FString::Printf(TEXT("%s: t_ns"), V.Name), Frame.Tns, V.ExpectedTns);
        TestEqual(FString::Printf(TEXT("%s: dur_us"), V.Name), Frame.DurUs, V.ExpectedDurUs);
        TestEqual(FString::Printf(TEXT("%s: fps"), V.Name), Frame.Fps, V.ExpectedFps);
        TestEqual(FString::Printf(TEXT("%s: chunk_size"), V.Name), Frame.ChunkSize, V.ExpectedChunkSize);
        TestEqual(FString::Printf(TEXT("%s: bs_count"), V.Name), Frame.BsCount, V.ExpectedBsCount);
        TestEqual(FString::Printf(TEXT("%s: flags"), V.Name), (int32)Frame.Flags, (int32)V.ExpectedFlags);
        TestEqual(FString::Printf(TEXT("%s: payload floats"), V.Name),
                  Frame.Payload.Num(),
                  V.ExpectedBsCount * V.ExpectedChunkSize);
        TestTrue(FString::Printf(TEXT("%s: jawOpen (idx 10)"), V.Name),
                 NearlyEqual(Frame.Payload[10], V.ExpectedJawOpen));
        TestTrue(FString::Printf(TEXT("%s: eyeBlinkLeft (idx 0)"), V.Name),
                 NearlyEqual(Frame.Payload[0], V.ExpectedBlinkL));
        // §5.1 Check B: crc32(whole frame incl. CRC) is constant
        TestEqual(FString::Printf(TEXT("%s: CRC residue"), V.Name),
                  FFaceContractDecoder::ComputeWholeFrameResidue(Bytes),
                  FFaceContractDecoder::CRC_RESIDUE);
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FFaceContractDecoder_NegativeVectors,
    "GorgonMetaHuman.FaceContractDecoder.NegativeVectors",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
    EAutomationTestFlags::ServerContext | EAutomationTestFlags::ProductFilter)

bool FFaceContractDecoder_NegativeVectors::RunTest(const FString& Parameters)
{
    for (const FNegativeVector& V : GNegativeVectors)
    {
        TArray<uint8> Bytes = HexToBytes(V.Hex);
        FFaceContractFrame Frame;
        FString Error;
        bool bRejected = false;
        // AddExpectedError keeps the automation log clean; the decoder also
        // logs the message itself, so match loosely on the canon prefix.
        AddExpectedError(FString::Printf(TEXT("FaceContract: %s"), V.ErrorPrefix),
                         EAutomationExpectedErrorFlags::Contains, 0);
        {
            // Decode must return false with the canon error prefix.
            bRejected = !FFaceContractDecoder::Decode(Bytes, Frame, &Error);
        }
        TestTrue(FString::Printf(TEXT("%s: rejected"), V.Name), bRejected);
        TestTrue(FString::Printf(TEXT("%s: error prefix"), V.Name),
                 Error.StartsWith(V.ErrorPrefix),
                 FString::Printf(TEXT("got: %s"), *Error));
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FFaceContractDecoder_StreamI1I2,
    "GorgonMetaHuman.FaceContractDecoder.StreamI1I2",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
    EAutomationTestFlags::ServerContext | EAutomationTestFlags::ProductFilter)

bool FFaceContractDecoder_StreamI1I2::RunTest(const FString& Parameters)
{
    int64 PrevSeq = -1;
    int64 PrevTns = -1;
    for (int32 i = 0; i < GStreamFrameCount; ++i)
    {
        TArray<uint8> Bytes = HexToBytes(GStreamFramesHex[i]);
        FFaceContractFrame Frame;
        FString Error;
        if (!FFaceContractDecoder::Decode(Bytes, Frame, &Error))
        {
            AddError(FString::Printf(TEXT("stream[%d] rejected: %s"), i, *Error));
            return false;
        }
        TestEqual(FString::Printf(TEXT("stream[%d]: seq"), i), Frame.Seq, GStreamSeqs[i]);
        TestEqual(FString::Printf(TEXT("stream[%d]: t_ns"), i), Frame.Tns, GStreamTns[i]);
        TestEqual(FString::Printf(TEXT("stream[%d]: flags"), i),
                  (int32)Frame.Flags, (int32)GStreamFlags[i]);
        if (PrevSeq >= 0)
        {
            TestEqual(FString::Printf(TEXT("stream[%d]: I1 seq +1"), i),
                      Frame.Seq, PrevSeq + 1);
            TestTrue(FString::Printf(TEXT("stream[%d]: I2 t_ns step"), i),
                     NearlyEqual64(Frame.Tns - PrevTns, GStreamDurNs));
        }
        PrevSeq = Frame.Seq;
        PrevTns = Frame.Tns;
    }
    return true;
}
"""


def render_hex_const(var_name, hexstr, per_line=2):
    parts = hex_parts(hexstr)
    lines = [f"const TCHAR* {var_name} ="]
    for i in range(0, len(parts), per_line):
        chunk = parts[i:i + per_line]
        lines.append("    " + " ".join(f'TEXT("{p}")' for p in chunk))
    lines[-1] += ";"
    return "\n".join(lines)


def render_cpp(doc):
    consts = []
    names = {}                       # vector name -> C++ const name

    def hexconst(vec_name, hexstr, prefix=""):
        cname = const_name(vec_name, prefix)
        names[vec_name] = cname
        consts.append(render_hex_const(cname, hexstr))

    for p in doc["positive"]:
        hexconst(p["name"], "".join(p["frame_hex_parts"]))
    for n in doc["negative"]:
        hexconst(n["name"], "".join(n["frame_hex_parts"]), prefix="Neg")
    for i, parts in enumerate(doc["stream"]["frames_hex_parts"]):
        hexconst(f"stream_frame_{i}", "".join(parts))

    data = []
    data.append("const FGoldenVector GGoldenVectors[] = {")
    for p in doc["positive"]:
        e = p["expect"]
        data.append(
            '    {{TEXT("{name}"), {cname}, {seq}LL, {tns}LL, {dur}i, '
            '{fps}i, {cs}i, {bs}i, {flags}, {jaw}f, {blink}f}},'.format(
                name=p["name"], cname=names[p["name"]], seq=e["seq"],
                tns=e["t_ns"], dur=e["dur_us"], fps=e["fps"],
                cs=e["chunk_size"], bs=e["bs_count"], flags=e["flags"],
                jaw=e["jawOpen"], blink=e["eyeBlinkLeft"]))
    data.append("};")
    data.append("")
    data.append("const FNegativeVector GNegativeVectors[] = {")
    for n in doc["negative"]:
        data.append('    {{TEXT("{0}"), {1}, TEXT("{2}")}},'.format(
            n["name"], names[n["name"]], n["error_prefix"]))
    data.append("};")
    data.append("")
    s = doc["stream"]
    data.append(f"const int32 GStreamFrameCount = {s['frame_count']};")
    data.append(f"const int64 GStreamDurNs = {s['dur_ns']}LL;")
    data.append("const int64 GStreamSeqs[] = {" + ", ".join(
        f"{x}LL" for x in s["seqs"]) + "};")
    data.append("const int64 GStreamTns[] = {" + ", ".join(
        f"{x}LL" for x in s["t_ns"]) + "};")
    data.append("const uint8 GStreamFlags[] = {" + ", ".join(
        f"0x{x:02X}" for x in s["flags"]) + "};")
    data.append("")
    data.append("// 6-frame stream hex:")
    data.append("const TCHAR* GStreamFramesHex[] = {")
    for i in range(s["frame_count"]):
        data.append(f"    {names[f'stream_frame_{i}']},")
    data.append("};")

    cpp = CPP_TEMPLATE.replace("__HEX_CONSTANTS__", "\n".join(consts))
    return cpp.replace("__VECTOR_DATA__", "\n".join(data))


def main():
    doc = build_vectors()
    with open(JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    cpp = render_cpp(doc)
    with open(CPP_PATH, "w", encoding="utf-8") as fh:
        fh.write(cpp)
    print(f"written {JSON_PATH} ({os.path.getsize(JSON_PATH)} bytes)")
    print(f"written {CPP_PATH} ({os.path.getsize(CPP_PATH)} bytes)")
    for p in doc["positive"]:
        e = p["expect"]
        full = "".join(p["frame_hex_parts"])
        print(f"  {p['name']:14s} bytes={len(full)//2:5d} "
              f"crc={e['crc']} jaw={e['jawOpen']:.4f}")
    for n in doc["negative"]:
        print(f"  {n['name']:28s} -> {n['error_prefix']}")


if __name__ == "__main__":
    main()
