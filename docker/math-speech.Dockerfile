# Math speech sidecar (math.convert, math.verbalize): MathJax converts LaTeX to
# MathML and MathCAT, compiled to WebAssembly, speaks MathML in pt-BR and
# writes math braille (CMU, Nemeth, UEB). One Node.js process serves both.
#
# MathJax is pinned by package-lock.json and MathCAT by commit in
# mathcat-wasm/Cargo.toml (with Cargo.lock). /version reports both and they
# enter the Toolbox cache key: bump them deliberately.

# --- MathCAT -> WebAssembly ---------------------------------------------------
FROM rust:1.90-slim-bookworm AS mathcat

# Must match the wasm-bindgen crate version in mathcat-wasm/Cargo.toml.
ARG WASM_BINDGEN_VERSION=0.2.128

RUN rustup target add wasm32-unknown-unknown && \
    cargo install wasm-bindgen-cli --version "${WASM_BINDGEN_VERSION}" --locked

WORKDIR /build
COPY docker/math-speech/mathcat-wasm/ ./
# wasm-bindgen emits a CommonJS module; the package.json marker keeps Node
# from reading it as ESM under the server's "type": "module".
RUN cargo build --release --locked --target wasm32-unknown-unknown && \
    wasm-bindgen --target nodejs --out-dir /pkg \
        target/wasm32-unknown-unknown/release/mathcat_wasm.wasm && \
    echo '{"type": "commonjs"}' > /pkg/package.json

# --- Runtime ------------------------------------------------------------------
FROM node:22-bookworm-slim

ENV NODE_ENV=production MATH_SPEECH_PORT=5009

WORKDIR /app
COPY docker/math-speech/package.json docker/math-speech/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=mathcat /pkg ./mathcat
COPY docker/math-speech/server.mjs docker/math-speech/smoke.mjs docker/math-speech/golden.json ./

USER node

# Smoke test: the image only builds if conversion, pt-BR speech, CMU braille
# and the error codes behave, and the speech of golden.json is unchanged.
RUN node smoke.mjs

EXPOSE 5009

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=10s \
    CMD node -e "fetch('http://127.0.0.1:5009/health').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"

CMD ["node", "server.mjs"]
