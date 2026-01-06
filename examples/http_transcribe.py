import sys
import requests

def main():
    if len(sys.argv) < 3:
        print("Usage: python http_transcribe.py <base_url> <wav_path>")
        print("Example: python http_transcribe.py http://localhost:8000 ./sample.wav")
        return 2

    base_url = sys.argv[1].rstrip("/")
    wav_path = sys.argv[2]

    with open(wav_path, "rb") as f:
        files = {"file": (wav_path.split("/")[-1], f, "audio/wav")}
        r = requests.post(f"{base_url}/api/transcribe", files=files, timeout=120)
        r.raise_for_status()
        print(r.json())
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
