"""gorgon_ws_bridge.py — plain WebSocket bridge feeding the GorgonMetaHuman
UE5 plugin (and any FaceContract v1.4.1 client).

This is the transport the plugin README documents: binary WebSocket messages
carry one FaceContract frame each; text messages carry small JSON control
payloads. There is NO LiveKit here — for the LiveKit Agents path use
livekit_bridge.py (Data Channel publishing, same wire bytes).

Run:
    python gorgon_ws_bridge.py --port 8765 --bs-count 72

Console commands:
    speech            — start a scripted utterance (SPEAKING → crossfade → idle)
    stop              — end the current utterance (hold-last tail → hangover)
    trigger <name>    — broadcast {"type":"trigger_reaction","reaction":<name>}
    metrics           — print per-connection Gorgon metrics
    quit              — shut the bridge down

Offline conformance (no network, no deps beyond the repo itself):
    python gorgon_ws_bridge.py --self-test

Wire behaviour (PROTOCOL.md):
  - one Gorgon per connection (I5: seq restarts at 0, fresh t_ns epoch)
  - frames are emitted by the fps-paced tick and sent as SINGLE binary
    messages (the UE5 plugin accumulates fragments until bIsLast)
  - text client→server JSON accepted: {"type":"stop"} and
    {"type":"trigger_reaction","reaction":"..."} — anything else ignored
  - server→client text: trigger_reaction notifications only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import threading
import time
import zlib
from typing import Dict, List, Optional

import facecontract_v141 as fc
from gorgon_glue import Gorgon
from gorgon_state import GorgonConfig
from livekit_bridge import BreathingIdleGenerator, IdleGeneratorConfig

log = logging.getLogger("gorgon.ws")


# ── scripted speech model (deterministic, NAME-addressed) ──────────────────────

class SpeechModelSim:
    """Deterministic utterance source for the WS bridge.

    Rows are addressed through fc.NAME_TO_INDEX (never raw indices — the
    channel-order guard test_retargeting_smoke.py T9 applies to every
    generator). Works at any bs_count: Core/Extended/Premium rows get the
    named articulation channels, test-scale levels (1,2,4,8) drive row[0].
    """

    def __init__(self, bs_count: int, chunk_size: int = 4):
        self.bs_count = bs_count
        self.chunk_size = chunk_size

    def _row(self, i: int) -> List[float]:
        n = self.bs_count
        row = [0.0] * n
        if n < 32:
            row[0] = 0.25 + 0.12 * math.sin(2 * math.pi * i / 4)
            return row
        idx = fc.NAME_TO_INDEX
        row[idx["jawOpen"]] = 0.30 + 0.10 * math.sin(2 * math.pi * i / 6)
        row[idx["mouthUpperUpLeft"]] = 0.12
        row[idx["mouthUpperUpRight"]] = 0.12
        row[idx["mouthLowerDownLeft"]] = 0.08
        row[idx["mouthLowerDownRight"]] = 0.08
        row[idx["mouthSmileLeft"]] = 0.05
        row[idx["mouthSmileRight"]] = 0.05
        return row

    def chunk(self, chunk_index: int) -> List[float]:
        rows = [self._row(chunk_index * self.chunk_size + f)
                for f in range(self.chunk_size)]
        return [v for row in rows for v in row]


class SessionDriver:
    """Drives ONE Gorgon instance for one client connection.

    All mutating calls happen on the event loop thread; tick() is advanced by
    the connection's send loop (one tick per chunk period).
    """

    def __init__(self, bs_count: int, fps: int = 30, chunk_size: int = 4):
        self.cfg = GorgonConfig(fps=fps, chunk_size=chunk_size)
        self.idle_gen = BreathingIdleGenerator(
            IdleGeneratorConfig(fps=fps, bs_count=bs_count,
                                chunk_size=chunk_size))
        self.model_queue: List[List[float]] = []
        self.gorgon = _QueueBackedGorgon(self.cfg, bs_count,
                                         self.idle_gen, self.model_queue)
        self.sim = SpeechModelSim(bs_count, chunk_size)
        self._t0_ns = time.time_ns()
        self.gorgon.on_ws_open(start_t_ns=0)
        # lazy crossfade wiring — same contract as GorgonLiveKitBridge
        self.idle_gen.set_tail_provider(
            lambda: self.gorgon.model_queue.last_popped)
        self._speaking = False
        self._chunks_sent = 0

    def now_us(self) -> int:
        return (time.time_ns() - self._t0_ns) // 1_000

    # ── scripted control (console / client JSON) ──────────────────────────

    def start_speech(self, chunks: int = 6) -> None:
        self.gorgon.on_vad_start(self.now_us())
        self.idle_gen.mark_utterance_start()
        for k in range(chunks):
            self.gorgon.on_model_chunk(self.now_us(), self.sim.chunk(k))
        self.gorgon.on_model_done(self.now_us())
        self._speaking = True

    def stop_speech(self) -> None:
        self.gorgon.on_model_done(self.now_us())
        self._speaking = False

    def tick(self, now_us: Optional[int] = None) -> None:
        """Advance one tick. Pass now_us explicitly (offline self-test drives
        a synthetic clock); wall-clock is used only when omitted."""
        self.gorgon.tick(self.now_us() if now_us is None else now_us)

    def take_frames(self) -> List[bytes]:
        out: List[bytes] = []
        while True:
            f = self.gorgon.take_frame()
            if f is None:
                return out
            out.append(f)


class _QueueBackedGorgon(Gorgon):
    """Gorgon variant whose model queue is a plain list owned by the driver
    (push/pop satisfy the ModelQueue protocol; FIFO)."""

    def __init__(self, config, bs_count, idle_gen, queue_list):
        self._queue_list = queue_list
        super().__init__(config, bs_count, idle_gen=idle_gen,
                         model_queue=_ListQueue(queue_list))


class _ListQueue:
    """FIFO ModelQueue that records the last consumed chunk — the crossfade
    tail source (gorgon_glue clears Gorgon._last_model_payload at the IDLE
    boundary before the idle generator runs; see RecordingModelQueue)."""

    def __init__(self, store: List[List[float]]):
        self._store = store
        self.last_popped: Optional[List[float]] = None

    def push(self, payload: List[float]) -> None:
        self._store.append(payload)

    def pop(self) -> Optional[List[float]]:
        if not self._store:
            return None
        payload = self._store.pop(0)
        self.last_popped = payload
        return payload


# ── self-test (offline conformance, no network) ──────────────────────────────

def self_test(bs_count: int = 32, fps: int = 30, chunk_size: int = 4) -> int:
    print(f"gorgon_ws_bridge self-test (bs_count={bs_count}, fps={fps}, "
          f"chunk_size={chunk_size})")
    failures: List[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"  [{'OK' if cond else 'FAIL'}] {name}"
              + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    drv = SessionDriver(bs_count, fps, chunk_size)
    frames: List[bytes] = []
    now = 0
    prev_seq: Optional[int] = None
    prev_t: Optional[int] = None
    flags_seen = set()

    drv.start_speech(chunks=6)
    for _ in range(24):
        drv.tick(now)                 # synthetic clock: hangover must expire
        now += drv.cfg.dur_ns // 1000
        for f in drv.take_frames():
            d = fc.decode(f)            # raises on any wire violation
            flags_seen.add(d["flags"])
            if prev_seq is not None:
                if d["seq"] != prev_seq + 1:
                    failures.append(f"seq gap {prev_seq}->{d['seq']}")
                if d["t_ns"] - prev_t != drv.cfg.dur_ns:
                    failures.append(f"t_ns step {prev_t}->{d['t_ns']}")
            prev_seq, prev_t = d["seq"], d["t_ns"]
            residue = zlib.crc32(f) & 0xFFFFFFFF
            if residue != fc.CRC_RESIDUE:
                failures.append(f"residue 0x{residue:08X}")
            frames.append(f)

    check("frames emitted", len(frames) >= 15, str(len(frames)))
    check("I1 seq strict +1",
          all(not s.startswith("seq gap") for s in failures))
    check("I2 t_ns strict step",
          all(not s.startswith("t_ns step") for s in failures))
    check("I4 CRC residue 0x2144DF1C",
          all(not s.startswith("residue") for s in failures))
    check("I3 both flags seen (SPEAKING + IDLE)",
          flags_seen == {fc.FLAG_IDLE, fc.FLAG_SPEAKING},
          str({hex(f) for f in flags_seen}))
    check("I5 first seq == 0", fc.decode(frames[0])["seq"] == 0)
    check("I6 bs_count on wire", fc.decode(frames[0])["bs_count"] == bs_count)

    # jawOpen must fade from the speech tail, not snap (server-side crossfade)
    jaw_idx = fc.NAME_TO_INDEX["jawOpen"] if bs_count >= 32 else 0
    idle_jaw = [fc.decode(f)["payload"][jaw_idx]
                for f in frames if fc.decode(f)["flags"] == fc.FLAG_IDLE]
    check("idle crossfade starts from the tail (no snap)",
          bool(idle_jaw) and idle_jaw[0] > 0.15,
          str(idle_jaw[:2] if idle_jaw else None))
    check("idle decays to breathing", bool(idle_jaw)
          and idle_jaw[-1] < 0.08, str(idle_jaw[-3:] if idle_jaw else None))

    # trigger JSON contract (what GorgonWebSocketClient::ProcessTrigger parses)
    trigger = json.dumps({"type": "trigger_reaction", "reaction": "curious"})
    doc = json.loads(trigger)
    check("trigger JSON shape",
          doc["type"] == "trigger_reaction" and doc["reaction"] == "curious")

    n_fail = len([s for s in failures if not s.startswith(("seq", "t_ns",
                                                           "residue"))])
    ok = not failures
    print("SELF-TEST OK" if ok else f"SELF-TEST FAILED ({n_fail})")
    return 0 if ok else 1


# ── async WebSocket server ──────────────────────────────────────────────────────

class WSBridgeServer:
    def __init__(self, host: str, port: int, bs_count: int, fps: int,
                 chunk_size: int):
        self.host = host
        self.port = port
        self.bs_count = bs_count
        self.fps = fps
        self.chunk_size = chunk_size
        self.clients: Dict[SessionDriver, object] = {}   # drv -> ws
        self.client_outbound: Dict[SessionDriver, asyncio.Queue] = {}
        self._stop = asyncio.Event()

    async def handle(self, ws, path=None) -> None:    # websockets 10..14+
        drv = SessionDriver(self.bs_count, self.fps, self.chunk_size)
        outbound: asyncio.Queue = asyncio.Queue()
        self.clients[drv] = ws
        self.client_outbound[drv] = outbound
        peer = getattr(ws, "remote_address", ("?", 0))
        log.info("client %s connected (bs_count=%d)", peer, self.bs_count)
        sender = asyncio.ensure_future(self.send_loop(ws, drv, outbound))
        try:
            async for message in ws:
                self._on_client_text(drv, message)
        except Exception:
            log.exception("client %s handler crashed", peer)
        finally:
            sender.cancel()
            self.clients.pop(drv, None)
            self.client_outbound.pop(drv, None)
            log.info("client %s disconnected", peer)

    def _on_client_text(self, drv: SessionDriver, message) -> None:
        try:
            doc = json.loads(message)
        except (TypeError, ValueError):
            return
        if not isinstance(doc, dict):
            return
        mtype = doc.get("type")
        if mtype == "stop":
            drv.stop_speech()
        elif mtype == "trigger_reaction" and "reaction" in doc:
            self.broadcast_trigger(doc["reaction"])

    def broadcast_trigger(self, reaction: str) -> None:
        """Queue a trigger_reaction JSON for every client. Safe from the
        console thread (call_soon_threadsafe) and from client JSON handlers —
        actual sending happens in the client's own send_loop (websockets
        forbids concurrent ws.send() calls)."""
        payload = json.dumps({"type": "trigger_reaction",
                              "reaction": reaction})
        for q in list(self.client_outbound.values()):
            q.put_nowait(payload)

    async def send_loop(self, ws, drv: SessionDriver,
                        outbound: asyncio.Queue) -> None:
        period = drv.cfg.dur_ns / 1e9
        next_deadline = time.monotonic()
        try:
            while True:
                drv.tick()
                for frame in drv.take_frames():
                    await ws.send(frame)          # one frame per binary msg
                while not outbound.empty():       # text: triggers/state
                    await ws.send(outbound.get_nowait())
                next_deadline += period
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_deadline = time.monotonic()
        except Exception:
            return

    async def serve(self) -> None:
        import websockets
        async with websockets.serve(self.handle, self.host, self.port,
                                    max_size=None):
            log.info("gorgon_ws_bridge listening on ws://%s:%d (bs_count=%d)",
                     self.host, self.port, self.bs_count)
            await self._stop.wait()

    def request_stop(self) -> None:
        self._stop.set()


# ── console ─────────────────────────────────────────────────────────────────────

def console_loop(server: WSBridgeServer, loop: asyncio.AbstractEventLoop) -> None:
    print("commands: speech | stop | trigger <name> | metrics | quit")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            line = "quit"
        if not line:
            continue
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "speech":
            for drv in list(server.clients):
                drv.start_speech()
            print("speech: utterance started")
        elif cmd == "stop":
            for drv in list(server.clients):
                drv.stop_speech()
            print("stop: utterance ended")
        elif cmd == "trigger":
            name = arg or "curious"
            loop.call_soon_threadsafe(server.broadcast_trigger, name)
            print(f"trigger: {name}")
        elif cmd == "metrics":
            for drv in list(server.clients):
                print(f"  {drv.gorgon.metrics()}")
            if not server.clients:
                print("  (no clients)")
        elif cmd == "quit":
            loop.call_soon_threadsafe(server.request_stop)
            return
        else:
            print(f"unknown command: {cmd}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Gorgon FaceContract WS bridge")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bs-count", type=int, default=72,
                    choices=sorted(fc.SUPPORTED_BS_COUNTS))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--chunk-size", type=int, default=4)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.self_test:
        return self_test(args.bs_count, args.fps, args.chunk_size)

    server = WSBridgeServer(args.host, args.port, args.bs_count, args.fps,
                            args.chunk_size)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    threading.Thread(target=console_loop, args=(server, loop),
                     daemon=True).start()
    try:
        loop.run_until_complete(server.serve())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
