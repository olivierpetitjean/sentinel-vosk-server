FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/olivierpetitjean/sentinel-vosk-server" \
      org.opencontainers.image.url="https://github.com/olivierpetitjean/sentinel-vosk-server" \
      org.opencontainers.image.title="sentinel-vosk-server" \
      org.opencontainers.image.description="FastAPI-based Speech-to-Text server using **Vosk**, exposing both **HTTP (Swagger/OpenAPI)** and **WebSocket streaming** on a single port." \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VOSK_SAMPLE_RATE=16000 \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
