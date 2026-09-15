# MinerU CPU-only image — lightweight alternative to the official vllm-based
# Dockerfile (docker/global/Dockerfile in the MinerU repo).
#
# The official image ships vllm + CUDA for the VLM backend (~15-20 GB). This
# variant installs only the `pipeline` backend (CPU inference), which is what
# the Acessilia Toolbox uses for document.structure.extract / layout / OCR.
# Result: ~4-6 GB instead of ~15-20 GB.
#
# Build:
#   docker build -t mineru:latest -f docker/mineru-cpu.Dockerfile .
#
# Run (see scripts/run-mineru.sh):
#   mineru-api --host 0.0.0.0 --port 8000

FROM python:3.12-slim

# System deps: libgl/libglib for OpenCV, fonts for CJK/Latin rendering.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        fonts-noto-core \
        fonts-noto-cjk \
        fontconfig \
        libgl1 \
        libglib2.0-0 \
        curl && \
    fc-cache -fv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Pin numpy<2: MinerU 2.x compiles against the NumPy 1.x ABI.
# six is a transitive dep MinerU's pipeline misses at runtime.
RUN pip install --no-cache-dir "numpy<2" six

# MinerU with the CPU pipeline backend only (no vllm, no CUDA, no torch CUDA).
RUN pip install --no-cache-dir "mineru[pipeline]" && \
    pip cache purge

# Non-root runtime user.
RUN useradd --create-home --shell /bin/bash mineru && \
    mkdir -p /home/mineru/.cache/modelscope && \
    chown -R mineru:mineru /home/mineru/.cache
USER mineru
WORKDIR /home/mineru

# Models download on first use into this volume.
ENV MINERU_MODEL_SOURCE=modelscope
VOLUME /home/mineru/.cache

EXPOSE 8000

# Health probe target (toolbox health_path is /openapi.json).
HEALTHCHECK --interval=15s --timeout=5s --retries=20 --start-period=120s \
    CMD curl -fsS http://localhost:8000/openapi.json || exit 1

ENTRYPOINT ["mineru-api", "--host", "0.0.0.0", "--port", "8000"]