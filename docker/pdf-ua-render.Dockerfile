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
# the template's text font (its math fonts, TeX Gyre DejaVu Math and the STIX
# Two Math fallback, come from tex-gyre-math and stix2-otf below); curl serves
# the healthcheck.
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
# install-tl and tlmgr download through curl with a stall limit: their default
# downloader (Perl LWP) can hang for good on a connection that stops mid-file,
# which froze CI builds. A transfer below 1 KB/s for 30 s is aborted and retried.
RUN export TL_DOWNLOAD_PROGRAM=curl \
        TL_DOWNLOAD_ARGS="--user-agent texlive/curl --retry 5 --retry-delay 5 --retry-all-errors --connect-timeout 30 --speed-limit 1024 --speed-time 30 --max-time 600 --fail --location --silent --output" && \
    mkdir /tmp/install-tl && \
    curl -fsSL --retry 5 --retry-all-errors --connect-timeout 30 --speed-limit 1024 --speed-time 30 \
        "${TEXLIVE_REPOSITORY}/install-tl-unx.tar.gz" \
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
        lualatex-math \
        pdfmanagement-testphase \
        stix2-otf \
        tagpdf \
        tex-gyre-math \
        unicode-math && \
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
COPY docker/pdf-ua-render/filters /app/filters
RUN chown -R appuser:appuser /app

USER appuser
WORKDIR /app

# Smoke test: the image only builds if the pinned toolchain renders the template,
# math included. The formula uses what recognizers and vision models write
# (\mathbb, \frac, \lim, aligned, amssymb names such as \varpropto that
# pdf_ua.tex maps for unicode-math): one unrenderable formula makes the core
# degrade every formula of a document to literal text. The PDF command mirrors
# the one /render runs in server.py, so a change to either shows up here.
# smoke_check.py checks that each expected alt is on a Formula element,
# including formulas in a table header and data cell, that the math font
# is embedded with \neq as U+2260 and math letters as base letters in the
# text layer, and (for the default smoke) that its lists are tagged with
# PDF 1.7 ListNumbering values.
COPY --chown=appuser:appuser docker/pdf-ua-render/smoke.json \
    docker/pdf-ua-render/table-smoke.json docker/pdf-ua-render/smoke_check.py /tmp/
RUN luaotfload-tool --update && \
    pandoc --from json --to pdf --standalone --syntax-highlighting=none \
        --pdf-engine=lualatex --template=/app/templates/pdf_ua.tex \
        --lua-filter=/app/filters/formula_alt.lua \
        -V lang=pt-BR -o /tmp/smoke.pdf /tmp/smoke.json && \
    python /tmp/smoke_check.py /tmp/smoke.pdf --require-double-struck --require-lists && \
    pandoc --from json --to pdf --standalone --syntax-highlighting=none \
        --pdf-engine=lualatex --template=/app/templates/pdf_ua.tex \
        --lua-filter=/app/filters/formula_alt.lua \
        -V lang=pt-BR -o /tmp/smoke-table.pdf /tmp/table-smoke.json && \
    python /tmp/smoke_check.py /tmp/smoke-table.pdf \
        "t igual a 2 mais h no cabeçalho" "x diferente de um na célula" && \
    printf '%s' '{"pandoc-api-version":[1,23,1],"meta":{},"blocks":[{"t":"Para","c":[{"t":"Str","c":"ok"}]}]}' \
        | pandoc --from json --to docx --standalone --sandbox --metadata lang=pt-BR \
            -o /tmp/smoke.docx && \
    rm /tmp/smoke.json /tmp/table-smoke.json /tmp/smoke_check.py /tmp/smoke.pdf /tmp/smoke-table.pdf /tmp/smoke.docx

EXPOSE 5007

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=20s \
    CMD curl -fsS http://localhost:5007/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "5007"]
