# §8 conformance vectors (.fc14)

Raw facecontract v1.4.1 frame streams. Binary files cannot be committed
directly through the text-only API used to seed this repository, so each
stream is available in two equivalent forms:

1. **Restore the stored bytes** — `*.fc14.b64` are the exact locked vectors,
   base64-encoded:

       python test_vectors/decode_b64.py

2. **Regenerate from the contract** — the encoder is deterministic; the output
   is byte-identical to the locked vectors (verified):

       python facecontract_v141.py --vectors

Either path is sufficient; both produce the same files.

| file | bytes | purpose |
|---|---|---|
| `silence.fc14` | 548 | minimal valid idle frame, Core-32 |
| `speech_ru_01.fc14` | 2604 | 3-frame utterance, Extended-52 |
| `gap_sequence.fc14` | 2604 | seq 0, 1, 3 — all valid frames; gap handling is a client policy (§6.2) |
| `idle_breathing_blink.fc14` | 4384 | 8 chunks Core-32, breathing + blink — input for `verify_behavior.mjs` |
| `corrupted_crc.fc14` | 548 | silence with one flipped payload byte — must be rejected by decode |

`verify_behavior.mjs` consumes `idle_breathing_blink.fc14`; run either restore
or generation step first.
