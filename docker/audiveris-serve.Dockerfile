# Audiveris optical music recognition sidecar.
#
# Wraps the Audiveris CLI (Java) behind a small FastAPI HTTP wrapper so the
# Toolbox image stays free of ML runtimes (constitution, principle 4).
#
# Audiveris is a desktop application: it links GTK3 and needs an X display even
# in batch mode, so the image ships Xvfb and the server starts it at boot.
#
# Build (from the repository root):
#   docker build -f docker/audiveris-serve.Dockerfile -t audiveris-serve:latest .

FROM ubuntu:24.04

ARG AUDIVERIS_VERSION=5.11.0
ARG AUDIVERIS_DEB=Audiveris-${AUDIVERIS_VERSION}-ubuntu24.04-x86_64.deb

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Runtime libraries Audiveris links against, plus the headless display stack.
# The .deb bundles its own JRE, so no JDK is installed here.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libasound2t64 \
        libbsd0 \
        libc6 \
        libgtk-3-0t64 \
        libmd0 \
        libx11-6 \
        libxau6 \
        libxcb1 \
        libxdmcp6 \
        libxext6 \
        libxi6 \
        libxrender1 \
        libxtst6 \
        python3 \
        python3-pip \
        python3-venv \
        tesseract-ocr \
        xdg-utils \
        xvfb \
        zlib1g && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Audiveris is distributed as a .deb; extract it instead of installing so the
# package scripts (desktop entries, icon caches) never run in the image.
RUN curl -fsSL -o /tmp/audiveris.deb \
        "https://github.com/Audiveris/audiveris/releases/download/${AUDIVERIS_VERSION}/${AUDIVERIS_DEB}" && \
    dpkg-deb --extract /tmp/audiveris.deb / && \
    rm -f /tmp/audiveris.deb && \
    test -x /opt/audiveris/bin/Audiveris

WORKDIR /app

RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn>=0.30" \
        "python-multipart>=0.0.9"

COPY docker/audiveris-serve/server.py /app/server.py

# Non-root runtime user; Audiveris writes its tessdata under $HOME.
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app /home/appuser

USER appuser
WORKDIR /app

ENV PATH="/opt/venv/bin:${PATH}" \
    AUDIVERIS_BIN=/opt/audiveris/bin/Audiveris \
    AUDIVERIS_DISPLAY=:99

EXPOSE 5003

HEALTHCHECK --interval=30s --timeout=5s --retries=10 --start-period=30s \
    CMD curl -fsS http://localhost:5003/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "5003"]
