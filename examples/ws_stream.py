import asyncio
import json
import sys
import websockets

CHUNK_BYTES = 3200  # ~100ms at 16kHz mono s16le (16000 * 2 bytes * 0.1)

async def main():
    if len(sys.argv) < 4:
        print("Usage: python ws_stream.py <ws_url> <pcm_s16le_path> <sample_rate>")
        print("Example: python ws_stream.py ws://localhost:8000/ws ./audio.pcm 16000")
        return 2

    ws_url = sys.argv[1]
    pcm_path = sys.argv[2]
    sample_rate = sys.argv[3]

    # allow passing ws://host/ws without query param
    if "sample_rate=" not in ws_url:
        sep = "&" if "?" in ws_url else "?"
        ws_url = f"{ws_url}{sep}sample_rate={sample_rate}"

    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
        async def receiver():
            try:
                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("type") == "partial" and data.get("text"):
                        print(f"partial: {data['text']}")
                    elif data.get("type") == "final":
                        print(f"final: {data.get('text','')}")
            except Exception:
                pass

        recv_task = asyncio.create_task(receiver())

        with open(pcm_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_BYTES)
                if not chunk:
                    break
                await ws.send(chunk)
                await asyncio.sleep(0.02)  # slight pacing (optional)

        await ws.close()
        await recv_task

    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
