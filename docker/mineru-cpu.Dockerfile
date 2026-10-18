# MinerU 4.x CPU-sidecar: Basic tier uses ONNX and needs no GPU/VLM.
# Build: ./scripts/build-mineru-image.sh 4.0.12

FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        fonts-noto-core fonts-noto-cjk fontconfig libgl1 libglib2.0-0 curl && \
    fc-cache -fv && apt-get clean && rm -rf /var/lib/apt/lists/*

ARG MINERU_VERSION=4.0.12
RUN pip install --no-cache-dir "mineru==${MINERU_VERSION}"

COPY patch-mineru-warp.py /tmp/patch-mineru-warp.py
RUN python3 /tmp/patch-mineru-warp.py && rm /tmp/patch-mineru-warp.py

RUN useradd --create-home --shell /bin/bash mineru && \
    mkdir -p /home/mineru/.mineru/models && \
    chown -R mineru:mineru /home/mineru/.mineru
USER mineru
WORKDIR /home/mineru

ENV MINERU_HOME=/home/mineru/.mineru
VOLUME /home/mineru/.mineru

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=20 --start-period=120s \
    CMD curl -fsS http://localhost:8000/v1/health || exit 1

ENTRYPOINT ["mineru-kit", "api-server", "--host", "0.0.0.0", "--port", "8000", "--tier", "basic"]
