"""verify_ue5_tests.py — validate the GENERATED UE5 conformance assets
against the Python canon (facecontract_v141.py):

  1. ue5_golden_vectors.json — join frame_hex_parts, decode/validate every
     frame (positives: fields + payload spot values + residue; negatives:
     rejected with the pinned error prefix; stream: I1/I2)
  2. FaceContractDecoderTests.cpp — re-parse every hex constant
     (concatenated TEXT() literals) and byte-check against the canon

Run from the repo root:

    python test_vectors/verify_ue5_tests.py
"""
import json
import os
import re
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

failures = []


def check(name, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def join_parts(parts):
    return "".join(parts)


def parse_cpp_hex_constants(src):
    """name -> full hex string, from `const TCHAR* Name = TEXT("...")...;`"""
    consts = {}
    for m in re.finditer(r'const TCHAR\* (\w+) =\s*(.*?);', src, re.S):
        name, body = m.group(1), m.group(2)
        hexes = re.findall(r'TEXT\("([0-9a-f]+)"\)', body)
        if hexes:
            consts[name] = "".join(hexes)
    return consts


def main():
    doc = json.load(open(JSON_PATH, encoding="utf-8"))
    src = open(CPP_PATH, encoding="utf-8").read()

    jaw_idx = fc.NAME_TO_INDEX["jawOpen"]
    blink_idx = fc.NAME_TO_INDEX["eyeBlinkLeft"]

    # ── JSON: positives ──
    for p in doc["positive"]:
        e = p["expect"]
        hexstr = join_parts(p["frame_hex_parts"])
        frame = bytes.fromhex(hexstr)
        name = p["name"]
        d = fc.decode(frame)                       # must not raise
        check(f"json {name}: len", len(frame) == 36 + e["bs_count"] * e["chunk_size"] * 4)
        check(f"json {name}: seq", d["seq"] == e["seq"])
        check(f"json {name}: t_ns", d["t_ns"] == e["t_ns"])
        check(f"json {name}: bs_count", d["bs_count"] == e["bs_count"])
        check(f"json {name}: flags", d["flags"] == e["flags"])
        check(f"json {name}: jawOpen", abs(d["payload"][jaw_idx] - e["jawOpen"]) < 1e-6)
        check(f"json {name}: eyeBlinkLeft", abs(d["payload"][blink_idx] - e["eyeBlinkLeft"]) < 1e-6)
        check(f"json {name}: payload_len", len(d["payload"]) == e["payload_len"])
        check(f"json {name}: crc", f"{fc.frame_crc(frame):08x}" == e["crc"])
        check(f"json {name}: residue",
              (zlib.crc32(frame) & 0xFFFFFFFF) == fc.CRC_RESIDUE)

    # ── JSON: negatives ──
    for ng in doc["negative"]:
        hexstr = join_parts(ng["frame_hex_parts"])
        frame = bytes.fromhex(hexstr)
        try:
            fc.decode(frame)
            check(f"json {ng['name']}: rejected", False, "accepted!")
        except fc.FaceContractError as exc:
            check(f"json {ng['name']}: '{ng['error_prefix']}...'",
                  str(exc).startswith(ng["error_prefix"]), str(exc))

    # ── JSON: stream I1/I2 ──
    s = doc["stream"]
    prev_seq = prev_tns = None
    for i, parts in enumerate(s["frames_hex_parts"]):
        frame = bytes.fromhex(join_parts(parts))
        d = fc.decode(frame)
        check(f"json stream[{i}]: seq", d["seq"] == s["seqs"][i])
        check(f"json stream[{i}]: t_ns", d["t_ns"] == s["t_ns"][i])
        check(f"json stream[{i}]: flags", d["flags"] == s["flags"][i])
        if prev_seq is not None:
            check(f"json stream[{i}]: I1", d["seq"] == prev_seq + 1)
            check(f"json stream[{i}]: I2", d["t_ns"] - prev_tns == s["dur_ns"])
        prev_seq, prev_tns = d["seq"], d["t_ns"]

    # ── .cpp: hex constants ──
    consts = parse_cpp_hex_constants(src)
    check("cpp: constant count",
          len(consts) == len(doc["positive"]) + len(doc["negative"])
          + s["frame_count"], str(sorted(consts)))

    golden_re = re.compile(
        r'\{TEXT\("([^"]+)"\), (\w+), (-?\d+)LL, (-?\d+)LL, (\d+)i, (\d+)i, '
        r'(\d+)i, (\d+)i, (\d+), ([\d.]+)f, ([\d.]+)f\}')
    neg_re = re.compile(
        r'\{TEXT\("([^"]+)"\), (\w+), TEXT\("([^"]+)"\)\}')

    goldens = golden_re.findall(src)
    negatives = neg_re.findall(src)
    stream_names = re.search(
        r'const TCHAR\* GStreamFramesHex\[\] = \{(.*?)\};', src, re.S).group(1)
    stream_const_names = re.findall(r'\w+', stream_names)

    print(f"parsed cpp: {len(goldens)} positives, {len(negatives)} negatives, "
          f"{len(stream_const_names)} stream refs")

    for (name, cname, seq, tns, dur, fps, cs, bs, flags, jaw, blink) \
            in goldens:
        hexstr = consts.get(cname, "")
        frame = bytes.fromhex(hexstr)
        d = fc.decode(frame)                       # must not raise
        check(f"cpp {name}: len", len(frame) == 36 + int(bs) * int(cs) * 4)
        check(f"cpp {name}: seq", d["seq"] == int(seq))
        check(f"cpp {name}: t_ns", d["t_ns"] == int(tns))
        check(f"cpp {name}: dur_us", d["dur_us"] == int(dur))
        check(f"cpp {name}: bs_count", d["bs_count"] == int(bs))
        check(f"cpp {name}: flags", d["flags"] == int(flags))
        check(f"cpp {name}: jawOpen", abs(d["payload"][jaw_idx] - float(jaw)) < 1e-6)
        check(f"cpp {name}: eyeBlinkLeft", abs(d["payload"][blink_idx] - float(blink)) < 1e-6)
        check(f"cpp {name}: residue",
              (zlib.crc32(frame) & 0xFFFFFFFF) == fc.CRC_RESIDUE)

    for (name, cname, prefix) in negatives:
        hexstr = consts.get(cname, "")
        frame = bytes.fromhex(hexstr)
        rejected = False
        try:
            fc.decode(frame)
        except fc.FaceContractError as exc:
            rejected = str(exc).startswith(prefix)
            if not rejected:
                check(f"cpp {name}: error prefix", False,
                      f"got '{exc}', want '{prefix}...'")
        check(f"cpp {name}: rejected with '{prefix}...'", rejected)

    dur_ns = int(re.search(r'const int64 GStreamDurNs = (\d+)LL;', src).group(1))
    seqs = [int(x) for x in re.search(r'GStreamSeqs\[\] = \{([^}]+)\};',
                                      src).group(1).replace("LL", "").split(",")]
    tns = [int(x) for x in re.search(r'GStreamTns\[\] = \{([^}]+)\};',
                                     src).group(1).replace("LL", "").split(",")]
    flags = [int(x, 16) for x in re.search(r'GStreamFlags\[\] = \{([^}]+)\};',
                                           src).group(1).split(",")]

    prev_seq = prev_tns = None
    for i, cname in enumerate(stream_const_names):
        hexstr = consts.get(cname, "")
        frame = bytes.fromhex(hexstr)
        d = fc.decode(frame)
        check(f"cpp stream[{i}]: seq", d["seq"] == seqs[i])
        check(f"cpp stream[{i}]: t_ns", d["t_ns"] == tns[i])
        check(f"cpp stream[{i}]: flags", d["flags"] == flags[i])
        if prev_seq is not None:
            check(f"cpp stream[{i}]: I1", d["seq"] == prev_seq + 1)
            check(f"cpp stream[{i}]: I2", d["t_ns"] - prev_tns == dur_ns)
        prev_seq, prev_tns = d["seq"], d["t_ns"]

    print("UE5 TEST DATA OK" if not failures else f"FAILED: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
