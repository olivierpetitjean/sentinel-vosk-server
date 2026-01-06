import argparse
import asyncio 
import contextlib
import json
import os
import time
import shutil
from typing import Optional, Tuple 
import sounddevice as sd
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen, Request
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
 
import warnings

# NOTE: `audioop` is part of the Python standard library (not a third-party dependency).
# It's deprecated and will be removed in Python 3.13+, but we keep it here on purpose
# because this is an *example* script and I want to avoid extra dependencies (numpy/scipy).
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        category=DeprecationWarning,
        message=r".*audioop.*",
    )
    import audioop

RATES_PREFERENCE = [48000, 44100]
ANSI_RESET = "\x1b[0m"
ANSI_GREEN = "\x1b[32m"
ANSI_YELLOW = "\x1b[33m"
ANSI_GRAY = "\x1b[90m"
ANSI_RED = "\x1b[31m"

def c(text: str, color: str) -> str:
    return f"{color}{text}{ANSI_RESET}"

def build_ws_url(base: str, sample_rate: int) -> str:
    if "sample_rate=" in base:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}sample_rate={sample_rate}"

def ws_to_health_url(ws_url: str) -> str:
    parts = urlsplit(ws_url)
    scheme = "https" if parts.scheme == "wss" else "http"
    # Always target /health
    return urlunsplit((scheme, parts.netloc, "/health", "", ""))

def fetch_health(ws_url: str, timeout_sec: float = 3.0) -> dict:
    url = ws_to_health_url(ws_url)
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))

def pick_input_format(device: Optional[int], prefer_16k: bool) -> Tuple[int, int]:
    # 1) Try native 16k first
    if prefer_16k:
        for ch in (1, 2):
            try:
                sd.check_input_settings(device=device, samplerate=16000, channels=ch, dtype="int16")
                return 16000, ch
            except Exception:
                pass

    # 2) Prefer 48k then 44.1k
    for sr in RATES_PREFERENCE:
        for ch in (1, 2):
            try:
                sd.check_input_settings(device=device, samplerate=sr, channels=ch, dtype="int16")
                return sr, ch
            except Exception:
                pass

    # 3) Fallback: device default samplerate
    dev = sd.query_devices(device, "input") if device is not None else sd.query_devices(sd.default.device[0], "input")
    default_sr = int(dev.get("default_samplerate", 48000))
    for ch in (1, 2):
        sd.check_input_settings(device=device, samplerate=default_sr, channels=ch, dtype="int16")
        return default_sr, ch

    raise RuntimeError("No suitable input format found for this device.")


class ConsoleUI:
    """
    Clean single-line status using ANSI clear-line + truncate to terminal width.
    Prints 'final' on its own line.
    """
    def __init__(self) -> None:
        self._status = ""
        self._lock = asyncio.Lock()

    def _term_width(self) -> int:
        return shutil.get_terminal_size((120, 20)).columns

    def _render(self, s: str) -> str:
        w = self._term_width()
        if w <= 10:
            return s[:w]
        
        return s[: w - 1]

    async def set_status(self, text: str) -> None:
        async with self._lock:
            self._status = text
            line = self._render(text)
            print("\x1b[2K\r" + line, end="", flush=True)

    async def println(self, line: str) -> None:
        async with self._lock:
            print("\x1b[2K\r", end="")
            print(line)
            if self._status:
                print("\x1b[2K\r" + self._render(self._status), end="", flush=True)


async def mic_stream_ws(
    ws_base_url: str,
    device: Optional[int],
    target_sr: int,
    chunk_ms: int,
    prefer_16k: bool,
    rms_threshold: int,
    idle_timeout_ms: int,
) -> None:
    ws_url = build_ws_url(ws_base_url, target_sr)

    in_sr, in_ch = pick_input_format(device, prefer_16k=prefer_16k)
    blocksize = max(1, int(in_sr * (chunk_ms / 1000.0)))  # frames

    ui = ConsoleUI()

    # Query server health (model + versions) before starting streaming
    try:
        h = fetch_health(ws_url)
        appv = h.get("app", {}).get("version", "?")
        voskv = h.get("engine", {}).get("version", "?")
        mname = h.get("model", {}).get("name", "?")
        mpath = h.get("model", {}).get("path", "?")
        await ui.println(f"[server] {ws_to_health_url(ws_url)}")
        await ui.println(f"[server] app={appv} vosk={voskv}")
        await ui.println(f"[server] model={mname} ({mpath})")
    except Exception as e:
        await ui.println(f"[server] health check failed: {type(e).__name__}: {e}")

    await ui.println(f"[audio] device={device} input_sr={in_sr} input_ch={in_ch} -> target_sr={target_sr} (mono, int16)")
    await ui.println(f"[ws]    url={ws_url}")
    await ui.println(f"")
    await ui.println("Starting... (CTRL+C to stop)")
    await ui.println(f"")

    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=80)

    conn_state = "connecting"
    last_voice_ts = time.monotonic()
    last_rms = 0
    last_partial = ""

    # State for audioop.ratecv
    rate_state = None
  
    def callback(indata, frames, time_info, sd_status):
        nonlocal last_voice_ts, last_rms, rate_state

        data = bytes(indata)

        # Ensure mono
        if in_ch == 2:
            data = audioop.tomono(data, 2, 0.5, 0.5)

        # RMS on mono int16
        rms = audioop.rms(data, 2)
        last_rms = rms
        if rms >= rms_threshold:
            last_voice_ts = time.monotonic()

        # Resample to target_sr
        if in_sr != target_sr:
            data, rate_state = audioop.ratecv(data, 2, 1, in_sr, target_sr, rate_state)

        # enqueue (drop if slow)
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass

    async def status_loop():
        while True: 
            try:
                # Not connected: show ONLY connection state (no audio state, no partial)
                if conn_state != "connected":
                    if conn_state.startswith("reconnecting"):
                        # conn_state example: "reconnecting (OSError)"
                        reason = conn_state[len("reconnecting"):].strip()
                        s = c("CONNECTION FAILED", ANSI_RED) + (f" {reason}" if reason else "")
                    else:
                        s = c("CONNECTING...", ANSI_YELLOW)

                    await ui.set_status(s)
                    await asyncio.sleep(0.2)
                    continue

                # Connected: show audio state + partial (fixed-width to avoid shifting)
                idle = (time.monotonic() - last_voice_ts) * 1000.0 >= idle_timeout_ms
                audio_state = "IDLE" if idle else "STRM"  # 4 chars

                left = c("CONNECTED", ANSI_GREEN).ljust(18)  # fixed width
                mid = c(audio_state, ANSI_GRAY if idle else ANSI_GREEN) 
                partial_snip = (last_partial[:40] + "…") if len(last_partial) > 40 else last_partial

                s = f"{left} | {mid} | {partial_snip}"

                await ui.set_status(s)
                await asyncio.sleep(0.2)

            except Exception as e:
                await ui.println(f"[status_loop error] {type(e).__name__}: {e}")
                return


    async def recv_loop(ws):
        nonlocal last_partial
        async for msg in ws:
            try:
                data = json.loads(msg)
                t = data.get("type")

                if t == "partial":
                    txt = data.get("text") or ""

                    if txt:
                        last_partial = txt
                        
                elif t == "final":
                    last_partial = ""
                    await ui.println(f"final: {data.get('text','')}")
            except Exception:
                pass

    async def send_loop(ws):
        # ensure we send all queued audio when connected
        while True:
            chunk = await q.get()
            try:
                await ws.send(chunk)
            except ConnectionClosedOK:
                return
            except ConnectionClosedError:
                return
            
    try:
        status_task = asyncio.create_task(status_loop())
 
        with sd.RawInputStream(
            samplerate=in_sr,
            blocksize=blocksize,
            device=device,
            channels=in_ch,
            dtype="int16",
            callback=callback,
        ):
            while True:
                try:
                    conn_state = "connecting"
                    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
                        conn_state = "connected"

                        # drop backlog to avoid catch-up on reconnect
                        while not q.empty():
                            try:
                                q.get_nowait()
                            except Exception:
                                break

                        rx = asyncio.create_task(recv_loop(ws))
                        tx = asyncio.create_task(send_loop(ws))

                        done, pending = await asyncio.wait([rx, tx], return_when=asyncio.FIRST_EXCEPTION)
                        for t in pending:
                            t.cancel()
                        for t in done:
                            exc = t.exception()
                            if exc:
                                raise exc

                except asyncio.CancelledError:
                    raise
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    conn_state = f"reconnecting ({type(e).__name__})"

    finally:
        status_task.cancel()
        with contextlib.suppress(Exception):
            await status_task

def main() -> int:
    p = argparse.ArgumentParser(description="Stream microphone audio to sentinel-vosk-server WebSocket.")
    p.add_argument("--ws", default="ws://localhost:8000/ws")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--target-sr", type=int, default=16000)
    p.add_argument("--chunk-ms", type=int, default=100)
    p.add_argument("--prefer-16k", action="store_true")
    p.add_argument("--rms-threshold", type=int, default=300)
    p.add_argument("--idle-timeout-ms", type=int, default=800)
    args = p.parse_args()

    try:
        asyncio.run(
            mic_stream_ws(
                ws_base_url=args.ws,
                device=args.device,
                target_sr=args.target_sr,
                chunk_ms=args.chunk_ms,
                prefer_16k=args.prefer_16k,
                rms_threshold=args.rms_threshold,
                idle_timeout_ms=args.idle_timeout_ms,
            )
        )
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
