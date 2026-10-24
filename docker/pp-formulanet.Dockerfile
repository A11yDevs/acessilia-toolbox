# PP-FormulaNet_plus-M formula recognition sidecar.
#
# PaddleOCR's formula module (Apache-2.0) on CPU, one crop per request. The
# weights are fetched at build time so the runtime user never writes to the
# model cache and a missing model fails the build instead of the first request.
#
# The model is pinned by package version and by name; /version reports the
# PaddleOCR and PaddlePaddle versions, which enter the execution cache key.

FROM python:3.11-slim

ARG PP_FORMULANET_MODEL=PP-FormulaNet_plus-M

# OpenCV needs libGL/libglib; PaddlePaddle needs libgomp for its CPU kernels.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# HOME points at the application directory so the build-time model download
# lands where the runtime user can read it after the chown below.
ENV HOME=/app \
    PYTHONUNBUFFERED=1 \
    PP_FORMULANET_MODEL=${PP_FORMULANET_MODEL}

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "python-multipart>=0.0.9" \
    "Pillow>=10.0" \
    "numpy<3" \
    "paddlepaddle==3.3.1" \
    "paddleocr==3.7.0" && \
    pip cache purge

# Load the pinned weights now. FormulaRecognition downloads them on first use,
# which the non-root runtime user could not do.
RUN python -c "from paddleocr import FormulaRecognition; FormulaRecognition(model_name='${PP_FORMULANET_MODEL}', device='cpu')"

RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

COPY docker/pp-formulanet/server.py /app/server.py
COPY docker/pp-formulanet/formula_result.py /app/formula_result.py
RUN chown appuser:appuser /app/server.py /app/formula_result.py

USER appuser
WORKDIR /app

EXPOSE 5010

# Loading Paddle and the formula model takes longer than the other sidecars.
HEALTHCHECK --interval=15s --timeout=5s --retries=20 --start-period=90s \
    CMD curl -fsS http://localhost:5010/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "5010"]
