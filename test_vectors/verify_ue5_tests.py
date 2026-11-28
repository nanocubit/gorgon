"""verify_ue5_tests.py — validate the GENERATED FaceContractDecoderTests.cpp
against the Python canon (facecontract_v141.py): every embedded hex string
is decoded/rejected with the pinned expectations, proving the C++ test data
is canon-correct. Run from the repo root:

    python test_vectors/verify_ue5_tests.py

71 checks: 4 positives x 9 field/payload/residue checks + 7 negatives +
6 stream frames (fields + I1/I2). Non-zero exit on any mismatch.
"""
import os
import re
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import facecontract_v141 as fc   # noqa: E402

CPP_PATH = os.path.join(ROOT, "Plugins", "GorgonMetaHuman", "Source",
                        "GorgonMetaHuman", "Private",
                        "FaceContractDecoderTests.cpp")

failures = []


def check(name, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


POS_TMPL = ('\\{TEXT\\("([^"]+)"\\), TEXT\\("([0-9a-f]+)"\\), (-?\\d+)LL, '
            '(-?\\d+)LL, (\\d+)i, (\\d+)i, (\\d+)i, (\\d+)i, (\\d+), '
            '([\\d.]+)f, ([\\d.]+)f\\}')
NEG_TMPL = ('\\{TEXT\\("([^"]+)"\\), TEXT\\("([0-9a-f]+)"\\), '
            'TEXT\\("([^"]+)"\\)\\}')


def main():
    src = open(CPP_PATH, encoding="utf-8").read()

    positives = re.findall(POS_TMPL, src)
    negatives = re.findall(NEG_TMPL, src)
    stream_block = re.search(
        r'const TCHAR\* GStreamFramesHex\[\] = \{(.*?)\};', src, re.S).group(1)
    stream_hex = re.findall(r'TEXT\("([0-9a-f]+)"\)', stream_block)

    print(f"parsed {len(positives)} positives, {len(negatives)} negatives, "
          f"{len(stream_hex)} stream frames")
    if len(positives) != 4 or len(negatives) != 7 or len(stream_hex) != 6:
        failures.append("parse-count")

    jaw_idx = fc.NAME_TO_INDEX["jawOpen"]
    blink_idx = fc.NAME_TO_INDEX["eyeBlinkLeft"]

    for (name, hexstr, seq, tns, dur, fps, cs, bs, flags, jaw, blink) \
            in positives:
        frame = bytes.fromhex(hexstr)
        d = fc.decode(frame)                       # must not raise
        check(f"{name}: len", len(frame) == 36 + int(bs) * int(cs) * 4,
              str(len(frame)))
        check(f"{name}: seq", d["seq"] == int(seq))
        check(f"{name}: t_ns", d["t_ns"] == int(tns))
        check(f"{name}: dur_us", d["dur_us"] == int(dur))
        check(f"{name}: bs_count", d["bs_count"] == int(bs))
        check(f"{name}: flags", d["flags"] == int(flags))
        check(f"{name}: jawOpen",
              abs(d["payload"][jaw_idx] - float(jaw)) < 1e-6,
              str(d["payload"][jaw_idx]))
        check(f"{name}: eyeBlinkLeft",
              abs(d["payload"][blink_idx] - float(blink)) < 1e-6)
        check(f"{name}: residue",
              (zlib.crc32(frame) & 0xFFFFFFFF) == fc.CRC_RESIDUE)

    for (name, hexstr, prefix) in negatives:
        frame = bytes.fromhex(hexstr)
        rejected = False
        try:
            fc.decode(frame)
        except fc.FaceContractError as exc:
            rejected = str(exc).startswith(prefix)
            if not rejected:
                check(f"{name}: error prefix", False,
                      f"got '{exc}', want '{prefix}...'")
        check(f"{name}: rejected with '{prefix}...'", rejected)

    dur_ns = int(re.search(r'const int64 GStreamDurNs = (\d+)LL;', src).group(1))
    seqs = [int(x) for x in re.search(r'GStreamSeqs\[\] = \{([^}]+)\};',
                                      src).group(1).replace("LL", "").split(",")]
    tns = [int(x) for x in re.search(r'GStreamTns\[\] = \{([^}]+)\};',
                                     src).group(1).replace("LL", "").split(",")]
    flags = [int(x, 16) for x in re.search(r'GStreamFlags\[\] = \{([^}]+)\};',
                                           src).group(1).split(",")]

    prev_seq = prev_tns = None
    for i, hexstr in enumerate(stream_hex):
        frame = bytes.fromhex(hexstr)
        d = fc.decode(frame)
        check(f"stream[{i}]: seq", d["seq"] == seqs[i])
        check(f"stream[{i}]: t_ns", d["t_ns"] == tns[i])
        check(f"stream[{i}]: flags", d["flags"] == flags[i])
        if prev_seq is not None:
            check(f"stream[{i}]: I1", d["seq"] == prev_seq + 1)
            check(f"stream[{i}]: I2", d["t_ns"] - prev_tns == dur_ns)
        prev_seq, prev_tns = d["seq"], d["t_ns"]

    print("UE5 TEST DATA OK" if not failures else f"FAILED: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
