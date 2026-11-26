#!/usr/bin/env python3
"""Restore the raw .fc14 conformance vectors from their .b64 siblings.

The API path used to seed this repository is text-only, so the binary §8
conformance streams are stored base64-encoded. This script decodes them back
to bytes. The fully equivalent alternative is to regenerate from the contract
itself (the encoder is deterministic; both paths produce identical files):

    python facecontract_v141.py --vectors

Usage:
    python test_vectors/decode_b64.py            # write *.fc14
    python test_vectors/decode_b64.py --check    # verify existing *.fc14 match
"""
import base64
import pathlib
import sys


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent
    check_only = "--check" in sys.argv[1:]
    ok = True
    for b64_path in sorted(here.glob("*.fc14.b64")):
        raw = base64.b64decode(b64_path.read_text())
        out = b64_path.with_suffix("")           # "name.fc14.b64" -> "name.fc14"
        if check_only:
            same = out.exists() and out.read_bytes() == raw
            print(f"{'OK  ' if same else 'FAIL'} {out.name} ({len(raw)} bytes)")
            ok &= same
        else:
            out.write_bytes(raw)
            print(f"wrote {out.name} ({len(raw)} bytes)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
