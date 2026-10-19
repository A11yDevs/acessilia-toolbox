# Export sidecar: Pandoc + a frozen TeX Live for document.export.pdf_ua, and the
# same Pandoc, without TeX, for document.export.docx.
#
# TeX Live is installed from the year's frozen "tlnet-final" repository, so a
# rebuild yields the same kernel and tagging packages instead of whatever the
# live mirror carries that day. Mixed package generations are exactly what broke
# PDF/UA locally (tagpdf newer than the LaTeX kernel). Bump TEXLIVE_YEAR or
# PANDOC_VERSION deliberately; /version reports both and they enter the cache key.

FROM python:3.12-slim

ARG TARGETARCH
ARG PANDOC_VERSION=3.9.0.2
ARG TEXLIVE_YEAR=2025
ARG TEXLIVE_REPOSITORY=https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/${TEXLIVE_YEAR}/tlnet-final

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# perl and xz drive install-tl; fontconfig lets luaotfload find DejaVu Sans,
# the template's font; curl serves the healthcheck.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fontconfig \
        fonts-dejavu-core \
        perl \
        xz-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN arch="${TARGETARCH:-$(dpkg --print-architecture)}" && \
    curl -fsSL -o /tmp/pandoc.deb \
        "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${arch}.deb" && \
    dpkg -i /tmp/pandoc.deb && \
    rm /tmp/pandoc.deb

COPY docker/pdf-ua-render/texlive.profile /tmp/texlive.profile
RUN mkdir /tmp/install-tl && \
    curl -fsSL "${TEXLIVE_REPOSITORY}/install-tl-unx.tar.gz" \
        | tar -xz -C /tmp/install-tl --strip-components=1 && \
    /tmp/install-tl/install-tl \
        --profile=/tmp/texlive.profile \
        --repository="${TEXLIVE_REPOSITORY}" && \
    tlmgr --repository "${TEXLIVE_REPOSITORY}" install \
        booktabs \
        bookmark \
        caption \
        etoolbox \
        firstaid \
        fontspec \
        geometry \
        latex-lab \
        pdfmanagement-testphase \
        tagpdf && \
    rm -rf /tmp/install-tl /tmp/texlive.profile

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "python-multipart>=0.0.9"

RUN useradd --create-home --shell /bin/bash appuser
# luaotfload keeps its font-name database here; building it once at image build
# time keeps the first request from paying for a full font scan.
ENV TEXMFVAR=/home/appuser/.texlive/texmf-var

COPY docker/pdf-ua-render/server.py /app/server.py
COPY docker/pdf-ua-render/templates /app/templates
RUN chown -R appuser:appuser /app

USER appuser
WORKDIR /app

# Smoke test: the image only builds if the pinned toolchain renders the template.
RUN luaotfload-tool --update && \
    printf '%s' '{"pandoc-api-version":[1,23,1],"meta":{},"blocks":[{"t":"Para","c":[{"t":"Str","c":"ok"}]}]}' \
        | pandoc --from json --to pdf --pdf-engine=lualatex \
            --template=/app/templates/pdf_ua.tex -o /tmp/smoke.pdf && \
    printf '%s' '{"pandoc-api-version":[1,23,1],"meta":{},"blocks":[{"t":"Para","c":[{"t":"Str","c":"ok"}]}]}' \
        | pandoc --from json --to docx --sandbox -o /tmp/smoke.docx && \
    rm /tmp/smoke.pdf /tmp/smoke.docx

EXPOSE 5007

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=20s \
    CMD curl -fsS http://localhost:5007/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "5007"]
