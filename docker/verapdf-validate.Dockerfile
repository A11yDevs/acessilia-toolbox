# PDF/UA validation sidecar (pdf.validate.pdf_ua): veraPDF CLI behind FastAPI.
#
# veraPDF is pinned by version and installer digest. Its rules decide the
# verdict, so /version reports the release and it enters the cache key: bump
# VERAPDF_VERSION (and VERAPDF_SHA256) deliberately.

FROM python:3.12-slim

ARG VERAPDF_VERSION=1.30.2
ARG VERAPDF_SHA256=6cc6341cb1af644044054b81f00a6590a7918abb18f762243de115258bcad838

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    VERAPDF_BIN=/opt/verapdf/verapdf \
    JAVA_OPTS="-Xmx1g"

# curl downloads the installer and serves the healthcheck.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        default-jre-headless \
        unzip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY docker/verapdf-validate/auto-install.xml /tmp/auto-install.xml
RUN release="${VERAPDF_VERSION%.*}" && \
    curl -fsSL -o /tmp/verapdf.zip \
        "https://software.verapdf.org/releases/${release}/verapdf-greenfield-${VERAPDF_VERSION}-installer.zip" && \
    echo "${VERAPDF_SHA256}  /tmp/verapdf.zip" | sha256sum -c - && \
    unzip -q /tmp/verapdf.zip -d /tmp/verapdf && \
    java -jar "/tmp/verapdf/verapdf-greenfield-${VERAPDF_VERSION}/verapdf-izpack-installer-${VERAPDF_VERSION}.jar" \
        /tmp/auto-install.xml && \
    test -x /opt/verapdf/verapdf && \
    rm -rf /tmp/verapdf /tmp/verapdf.zip /tmp/auto-install.xml

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "python-multipart>=0.0.9"

RUN useradd --create-home --shell /bin/bash appuser
COPY docker/verapdf-validate/server.py docker/verapdf-validate/smoke.py /app/
RUN chown -R appuser:appuser /app

USER appuser
WORKDIR /app

# Smoke test: the image only builds if veraPDF validates and its report parses.
RUN python /app/smoke.py

EXPOSE 5008

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=20s \
    CMD curl -fsS http://localhost:5008/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "5008"]
