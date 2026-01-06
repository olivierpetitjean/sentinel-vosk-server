from __future__ import annotations

import contextlib
import json
import logging
import os
import wave
import importlib.metadata
from io import BytesIO
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketState
from vosk import KaldiRecognizer, Model

APP_NAME = "sentinel-vosk-server"
APP_VERSION = "1.0.0"
VOSK_VERSION = importlib.metadata.version("vosk")

logger = logging.getLogger(APP_NAME)

DEFAULT_SAMPLE_RATE = int(os.getenv("VOSK_SAMPLE_RATE", "16000"))

# Model resolution (no defaults):
# 1) If VOSK_MODEL_PATH is set, use it as-is
# 2) Else require both VOSK_MODELS_DIR and VOSK_MODEL and join them
_env_model_path = os.getenv("VOSK_MODEL_PATH")
_env_models_dir = os.getenv("VOSK_MODELS_DIR")
_env_model_name = os.getenv("VOSK_MODEL")

if _env_model_path:
    MODEL_PATH = _env_model_path
else:
    if not _env_models_dir or not _env_model_name:
        raise RuntimeError(
            "Missing model configuration. Provide either:\n"
            "- VOSK_MODEL_PATH=/models/<model_folder>\n"
            "or\n"
            "- VOSK_MODELS_DIR=/models AND VOSK_MODEL=<model_folder>"
        )
    MODEL_PATH = os.path.join(_env_models_dir, _env_model_name)

app = FastAPI(title=APP_NAME, version=APP_VERSION)

_model: Optional[Model] = None


def get_model() -> Model:
    """
    Return the singleton Vosk model.
    The model is loaded at startup; this function assumes it is ready.
    """
    global _model
    if _model is None:
        # Should never happen if startup succeeded, but keep a safe guard.
        raise RuntimeError("Vosk model is not loaded (startup not completed or failed).")
    return _model


@app.on_event("startup")
def startup_load_model() -> None:
    """
    Load the Vosk model once at startup.

    IMPORTANT:
    - If this raises, FastAPI startup fails and Uvicorn will not serve HTTP/WS endpoints.
    - This matches the requirement: "do not launch API/WS until the engine is OK".
    """
    global _model

    if not os.path.isdir(MODEL_PATH):
        raise RuntimeError(f"Vosk model folder not found: {MODEL_PATH}")

    logger.info("Loading Vosk model from: %s", MODEL_PATH)
    _model = Model(MODEL_PATH)
    logger.info("Vosk model loaded successfully.")


@app.get("/health")
def health():
    model_name = os.path.basename(os.path.normpath(MODEL_PATH))
    return {
        "status": "ok",
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "engine": {"name": "vosk", "version": VOSK_VERSION},
        "model": {"name": model_name, "path": MODEL_PATH},
        "defaults": {"sample_rate": DEFAULT_SAMPLE_RATE},
    }


@app.post("/api/transcribe")
async def transcribe_wav(
    file: UploadFile = File(...),
    max_seconds: int = Query(60, ge=1, le=3600),
):
    """
    Upload a WAV PCM file and return the final transcription.

    Supported:
    - WAV container
    - 16-bit PCM (sampwidth=2)
    - mono or stereo

    Recommended input:
    - 16 kHz, mono, 16-bit PCM WAV

    Note:
    - No resampling is performed here. The recognizer uses the WAV's sample rate.
    """
    if not (file.filename or "").lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Only .wav is supported (WAV/PCM).")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")

    try:
        with wave.open(BytesIO(data), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()

            duration = nframes / float(framerate) if framerate else 0.0
            if duration > max_seconds:
                raise HTTPException(status_code=413, detail=f"Audio too long (> {max_seconds}s).")

            if sampwidth != 2:
                raise HTTPException(status_code=400, detail="Only 16-bit PCM WAV is supported (sampwidth=2).")

            if channels not in (1, 2):
                raise HTTPException(status_code=400, detail="Only mono/stereo WAV is supported.")

            rec = KaldiRecognizer(get_model(), framerate)
            rec.SetWords(True)

            chunk_size_frames = 4000
            while True:
                chunk = wf.readframes(chunk_size_frames)
                if not chunk:
                    break
                rec.AcceptWaveform(chunk)

            result = json.loads(rec.FinalResult() or "{}")
            return JSONResponse(
                {
                    "text": result.get("text", ""),
                    "result": result.get("result", []),
                    "sample_rate": framerate,
                    "channels": channels,
                    "duration_sec": duration,
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid WAV: {e}") from e


@app.websocket("/ws")
async def ws_streaming_stt(
    ws: WebSocket,
    sample_rate: int = Query(DEFAULT_SAMPLE_RATE, ge=8000, le=48000),
):
    """
    WebSocket streaming STT.

    Protocol:
    - Client sends BINARY frames: raw PCM S16LE (mono) at `sample_rate`
    - Server sends JSON text frames:
        {"type":"partial","text":"..."}
        {"type":"final","text":"...","result":[...]}
    """
    await ws.accept()

    rec = KaldiRecognizer(get_model(), float(sample_rate))
    rec.SetWords(True)

    try:
        while True:
            # Using receive_bytes() is the simplest/cleanest:
            # it raises WebSocketDisconnect when the client closes,
            # avoiding Starlette's "disconnect message already received" error.
            audio = await ws.receive_bytes()

            if rec.AcceptWaveform(audio):
                r = json.loads(rec.Result() or "{}")
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "final",
                            "text": r.get("text", ""),
                            "result": r.get("result", []),
                        }
                    )
                )
            else:
                r = json.loads(rec.PartialResult() or "{}")
                await ws.send_text(json.dumps({"type": "partial", "text": r.get("partial", "")}))

    except WebSocketDisconnect:
        # Client closed: best effort flush (only if still connected)
        with contextlib.suppress(Exception):
            if ws.client_state == WebSocketState.CONNECTED:
                r = json.loads(rec.FinalResult() or "{}")
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "final",
                            "text": r.get("text", ""),
                            "result": r.get("result", []),
                        }
                    )
                )
    finally:
        with contextlib.suppress(Exception):
            await ws.close()
