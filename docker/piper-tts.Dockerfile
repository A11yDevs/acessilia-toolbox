# Piper lightweight neural TTS sidecar
# Designed for local offline speech synthesis via ONNX Runtime (< 0.1 RTF on CPU).

FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        espeak-ng && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "python-multipart>=0.0.9" \
    "piper-tts>=1.2.0" && \
    pip cache purge

# Create models directory and non-root runtime user
RUN useradd --create-home --shell /bin/bash appuser && \
    mkdir -p /models && \
    chown -R appuser:appuser /home/appuser /app /models

COPY docker/piper-tts/server.py /app/server.py
RUN chown appuser:appuser /app/server.py

USER appuser
WORKDIR /app

ENV PIPER_MODELS_DIR=/models
ENV PIPER_DEFAULT_VOICE=pt_BR-faber-medium

EXPOSE 5012

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=10s \
    CMD curl -fsS http://localhost:5012/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "5012"]
