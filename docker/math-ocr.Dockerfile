# RapidLaTeXOCR lightweight math recognition sidecar
# Designed for cropped formula image chips. Ultra-fast CPU inference (< 0.5s), < 150MB RAM.

FROM python:3.11-slim

# System dependencies for OpenCV & image processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install lightweight dependencies
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "python-multipart>=0.0.9" \
    "Pillow>=10.0" \
    requests \
    pyyaml \
    "rapid_latex_ocr>=0.1.1" && \
    pip cache purge

# Non-root runtime user for security and consistency
RUN useradd --create-home --shell /bin/bash appuser && \
    mkdir -p /home/appuser/.cache && \
    chown -R appuser:appuser /home/appuser /app

# Copy server application
COPY docker/math-ocr/server.py /app/server.py
RUN chown appuser:appuser /app/server.py

USER appuser
WORKDIR /app

EXPOSE 5004

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=10s \
    CMD curl -fsS http://localhost:5004/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "5004"]
