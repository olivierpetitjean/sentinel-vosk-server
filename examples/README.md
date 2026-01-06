Examples

This folder contains small Python clients to test sentinel-vosk-server (HTTP + WebSocket).
All commands below include both Windows and Linux variants, plus URL examples.

------------------------------------------------------------
0) Prerequisites
------------------------------------------------------------

Server must be running first.

Example URLs:
- Local:  http://localhost:8000
- Remote: http://10.0.10.2:8000
- WS local:  ws://localhost:8000/ws
- WS remote: ws://10.0.10.2:8000/ws

Python dependencies are installed from requirements.txt.

Windows (PowerShell):
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

Linux:
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

Note (Linux):
If sounddevice fails to install/run, you may need PortAudio dev libs:
sudo apt-get update
sudo apt-get install -y portaudio19-dev

------------------------------------------------------------
1) HTTP transcription (WAV upload)
------------------------------------------------------------

The HTTP API accepts WAV (PCM 16-bit recommended).
If your WAV is already mono / 16-bit / 16kHz, you can send it directly.

Example URL:
- http://localhost:8000/api/transcribe
- http://10.0.10.2:8000/api/transcribe

Windows:
.\.venv\Scripts\python http_transcribe.py http://localhost:8000 ..\sample.wav
.\.venv\Scripts\python http_transcribe.py http://10.0.10.2:8000 ..\sample.wav

Linux:
python http_transcribe.py http://localhost:8000 ../sample.wav
python http_transcribe.py http://10.0.10.2:8000 ../sample.wav

------------------------------------------------------------
2) WebSocket streaming (file -> WS)
------------------------------------------------------------

Important:
The WS endpoint expects raw PCM S16LE mono bytes.
If you pass a .wav file directly, it includes a WAV header.
It may still "work", but it is not the correct format.

Recommended: convert WAV -> PCM before streaming.

Example WS URLs:
- ws://localhost:8000/ws?sample_rate=16000
- ws://10.0.10.2:8000/ws?sample_rate=16000

Convert WAV -> PCM (S16LE mono 16kHz)

Windows:
ffmpeg -i ..\sample.wav -ac 1 -ar 16000 -f s16le audio.pcm

Linux:
ffmpeg -i ../sample.wav -ac 1 -ar 16000 -f s16le audio.pcm

Stream the PCM file:

Windows:
.\.venv\Scripts\python ws_stream.py ws://localhost:8000/ws .\audio.pcm 16000
.\.venv\Scripts\python ws_stream.py ws://10.0.10.2:8000/ws .\audio.pcm 16000

Linux:
python ws_stream.py ws://localhost:8000/ws ./audio.pcm 16000
python ws_stream.py ws://10.0.10.2:8000/ws ./audio.pcm 16000

------------------------------------------------------------
3) List audio input devices
------------------------------------------------------------

This script lists INPUT devices only and probes common sample rates (44100 / 48000).

Windows:
.\.venv\Scripts\python list_audio_devices.py

Linux:
python list_audio_devices.py

------------------------------------------------------------
4) WebSocket streaming (microphone -> WS, continuous)
------------------------------------------------------------

This script captures microphone audio continuously and streams it to the WS endpoint.
It supports devices that do NOT provide 16kHz by capturing at 48k/44.1k and resampling to 16k.

Example WS URLs:
- ws://localhost:8000/ws
- ws://10.0.10.2:8000/ws

Run with default input device:

Windows:
.\.venv\Scripts\python mic_stream_ws.py --ws ws://localhost:8000/ws --target-sr 16000

Linux:
python mic_stream_ws.py --ws ws://localhost:8000/ws --target-sr 16000

Run with a specific input device index (example: 6):

Windows:
.\.venv\Scripts\python mic_stream_ws.py --device 6 --ws ws://localhost:8000/ws --target-sr 16000

Linux:
python mic_stream_ws.py --device 6 --ws ws://localhost:8000/ws --target-sr 16000

Run against a remote server (example: 10.0.10.2):

Windows:
.\.venv\Scripts\python mic_stream_ws.py --ws ws://10.0.10.2:8000/ws --target-sr 16000

Linux:
python mic_stream_ws.py --ws ws://10.0.10.2:8000/ws --target-sr 16000

Optional flags:
- --chunk-ms 100            (default 100)
- --rms-threshold 300       (speech energy threshold)
- --idle-timeout-ms 800     (switch to IDLE after this silence duration)
- --prefer-16k              (try opening device at 16k first, if supported)

------------------------------------------------------------
5) Troubleshooting
------------------------------------------------------------

- "ModuleNotFoundError: websockets" or "sounddevice":
  You did not install requirements in the active Python environment.
  Re-run:
  python -m pip install -r requirements.txt

- WS works but file streaming from WAV gives weird results:
  Convert to PCM and stream the .pcm (see section 2).

- No device / input errors on Linux:
  Install PortAudio:
  sudo apt-get install -y portaudio19-dev

- Remote URL does not connect:
  Check firewall/port mapping. Server must expose TCP 8000.
