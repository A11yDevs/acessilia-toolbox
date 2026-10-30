# Tools and Providers

## Overview

The Toolbox exposes capabilities, not vendor APIs. Providers marked
**[implemented]** are wired in `providers-config.yaml`; the rest remain
roadmap candidates.

| Family        | Capability examples                                     | Providers                                  | Status        |
|---------------|---------------------------------------------------------|--------------------------------------------|---------------|
| Document AI   | `document.structure.extract`, `document.layout.analyze` | docling-serve, MinerU                      | [implemented] |
| OCR           | `document.ocr`                                          | docling-serve, MinerU (PaddleOCR, Surya: roadmap) | [implemented] |
| PDF           | `pdf.split`, `pdf.render` (v2: clip, `max_side`, PNG/JPEG; [pdf-render.md](pdf-render.md)), `document.text.spans` ([text-spans.md](text-spans.md)) | PyMuPDF | [implemented] |
| Tables        | table extraction inside `document.structure.extract`    | Docling (TableFormer), MinerU              | [implemented] |
| Conversion    | `document.convert`                                      | Pandoc                                     | roadmap       |
| Mathematics   | `math.recognize`, `math.convert`, `math.verbalize`      | docling-serve, PP-FormulaNet, math-speech (MathJax, MathCAT) | [implemented] |
| Media triage  | `media.inspect`                                         | pure-python (Pillow, in-process, optional extra) | [implemented] |
| Music         | `music.omr`                                             | homr (in-process), Audiveris (sidecar)     | [implemented] |
| Page layout   | `image.page.orient`                                     | RapidOCR (sidecar)                         | [implemented] |
| Vision-language | `document.ocr.page`, `document.adjudicate.page`, `document.adjudicate.region`, `table.recognize` | OpenAI-compatible VLM server (llama.cpp, vLLM) | [implemented] |
| Chemistry     | `chem.recognize`, `chem.convert`                        | docling-serve VLM (Granite Vision), pure-python mhchem | [implemented] |
| Accessibility | `accessibility.mark`                                    | pure-python (in-process)                   | [implemented] |
| PDF/UA        | `document.export.pdf_ua`, `pdf.validate.pdf_ua`         | Pandoc + TeX Live, veraPDF (sidecars)      | [implemented] |
| DOCX          | `document.export.docx`                                  | Pandoc (PDF/UA sidecar)                    | [implemented] |
| Code          | `code.normalize`                                        | pure-python (in-process)                   | [implemented] |
| Text          | `text.postprocess`                                      | pure-python (in-process)                   | [implemented] |
| Datasets      | `dataset.list`, `dataset.get_item`, `dataset.sync`, ... | GitHub, HuggingFace (in-process, optional) | [implemented] |
| Artifacts     | `artifact.store`, `artifact.retrieve`                   | MinIO, S3                                  | [implemented] |
| Cache         | `cache.get`, `cache.put`                                | Valkey, Redis                              | [implemented] |
| Metadata      | `artifact.metadata.extract`                             | ExifTool                                   | roadmap       |
| Security      | `artifact.scan`                                         | ClamAV                                     | roadmap       |
| Speech        | `speech.recognize`, `speech.synthesize`                 | sherpa-onnx, whisper.cpp, Piper            | roadmap       |

## Docling / docling-serve

The original Structure Extractor established a useful pattern: run
Docling externally through docling-serve to isolate PyTorch/model
dependencies and allow independent scaling. The Toolbox should retain
this approach, but Docling becomes one provider of normalized
capabilities.

Potential capabilities:

``` text
document.structure.extract
document.layout.analyze
table.extract
```

## MinerU

MinerU can provide overlapping document-understanding capabilities.
Overlap is useful for experimentation, fallback, and explicit agent
policy, but providers are interchangeable only when they satisfy the
same capability contract.

## OCR

OCR should be independently addressable rather than hidden only inside
document-understanding providers. This makes scanned-document pipelines
composable and enables controlled comparisons.

## Pandoc

Pandoc is a strong candidate for deterministic document conversion. It
should be treated as a renderer/converter rather than the canonical
internal document representation.

## Mathematics

Separate concerns:

``` text
math.recognize : image -> LaTeX (docling-serve, PP-FormulaNet, RapidLaTeXOCR)
math.convert   : LaTeX -> MathML (math-speech, MathJax); MathML -> LaTeX (in-process mapper)
math.verbalize : LaTeX -> pt-BR speech (math-speech, MathJax + MathCAT)
```

`math.recognize` has three providers. `pp-formulanet` runs PaddleOCR's
PP-FormulaNet_plus-M on CPU and is the recommended chip model: it is 2D-aware,
so matrices and vector notation survive recognition. `rapid-latex-ocr` is the
lighter, faster alternative, but its 1D decoder collapses matrices and drops
accents such as `\vec`. `docling-math` goes through docling-serve and is meant
for formula groups found in a document, not for a cropped chip.

These capabilities accept `artifact/latex@1`, a JSON object carrying the
expression under `latex`:

``` json
{ "latex": "x^2+1" }
```

`text/plain` is accepted too, and then the body is the expression itself. The
JSON form is the one to prefer: it is the declared schema and it leaves room for
`confidence`, `page` and `bbox` when the expression came from recognition.

Note: UniMERNet — the formula recognition model used internally by
MinerU's pipeline backend — is reached through `document.structure.extract`
and `math.recognize` (docling-math); the Toolbox does not run it as a
standalone service.

## Tables

Table extraction is exposed through `document.structure.extract` with
Docling's TableFormer model. The `table_mode` option (`fast` |
`accurate`) can be set in the provider's `config` (providers-config.yaml)
or overridden per request via the `table_mode` parameter. Setting
`pipeline: vlm` (optionally with `vlm_engine`) switches docling-serve to
the Granite Vision backend.

## Music (optical music recognition)

``` text
music.omr : sheet-music image/PDF -> MusicXML or MEI
```

Two interchangeable providers:

- `audiveris` — HTTP adapter for the `audiveris-serve` sidecar container
  (Java Audiveris + Tesseract behind a small REST wrapper). Configure via
  `AUDIVERIS_SERVE_URL`. **This is the only provider usable in the published
  image**: build the sidecar with
  `docker build -f docker/audiveris-serve.Dockerfile -t audiveris-serve:latest .`
  and start it with `docker compose up -d audiveris-serve`.
- `homr` — runs the homr Python package in-process (install the
  `music` extra: `pip install .[music]`). Only viable for a local, derived
  install: `homr` depends on `rapidocr`, which the Delivery guardrail forbids in
  the published image, so the `music` extra can never be part of it.

Audiveris is a desktop application: the sidecar runs it in batch mode under
Xvfb, and it exports compressed MusicXML (`.mxl`), which the sidecar unpacks
before returning the score.

## Page orientation

``` text
image.page.orient : raster page image -> detected angle, optionally upright image
```

A single provider, `rapidocr-orient`, runs RapidOCR with ONNX Runtime in a
sidecar container (`docker/rapidocr-orient.Dockerfile`). Configure the endpoint
with `RAPIDOCR_ORIENT_URL`.

Detection combines the OCR angle classifier — which resolves the 180° case that
horizontal geometry cannot reveal — with a comparison of how well the page reads
at 0°, 90° and 270°.

Set `parameters={"auto_rotate": true}` when extracting structure from an image to
run this as opt-in preprocessing. The decision is recorded in
`provenance.orientation`; if the sidecar is unavailable the extraction continues
unrotated. See [rapidocr-orient.md](rapidocr-orient.md).

## PDF/UA export

``` text
document.export.pdf_ua : Pandoc JSON AST -> tagged PDF/UA-1, stored as an artifact
```

A single provider, `pandoc-pdf-ua`, calls the `pdf-ua-render` sidecar
(`docker/pdf-ua-render.Dockerfile`), which pins Pandoc and a frozen TeX Live
release. Configure the endpoint with `PDF_UA_RENDER_URL`. The PDF is returned by
reference, so the capability needs an artifact store. See
[pdf-ua-export.md](pdf-ua-export.md).

## DOCX export

``` text
document.export.docx : Pandoc JSON AST -> Word document, stored as an artifact
```

The `pandoc-docx` provider calls the `/render/docx` route of the same
`pdf-ua-render` sidecar, which runs its pinned Pandoc with `--sandbox` and no
TeX. The DOCX is returned by reference. See [docx-export.md](docx-export.md).

## PDF/UA validation

``` text
pdf.validate.pdf_ua : PDF -> PDF/UA-1 (or PDF/UA-2) validation report
```

A single provider, `verapdf`, calls the `verapdf-validate` sidecar
(`docker/verapdf-validate.Dockerfile`), which pins the veraPDF CLI. Configure
the endpoint with `VERAPDF_VALIDATE_URL`. A non-compliant PDF is a successful
result whose report lists the failed rules. See
[pdf-ua-validation.md](pdf-ua-validation.md).

## Vision-language model

``` text
document.ocr.page          : page image -> Markdown transcription, finish reason
document.adjudicate.page   : page image + two candidate transcriptions -> p(second is more faithful)
document.adjudicate.region : region crop + OCR hypotheses -> exact transcription ("" when unreadable)
table.recognize            : table crop (+ provider tables as hints) -> greedy + seeded HTML generations
```

One adapter, `OpenAiCompatibleVlmProvider`
(`src/acessilia_toolbox/providers/openai_vlm.py`), serves the four
capabilities from any server that implements `POST /v1/chat/completions` with
image inputs: llama.cpp's `llama-server`, vLLM, and others. The provider entry
`qwen3-vl-8b` in `providers-config.yaml` points it at the production server
through `VLM_SERVE_URL` (use the internal network address, not a public
hostname) and sends `VLM_API_KEY` as a Bearer token when it is set.

The prompts and sampling are the ones validated in the Dr.DocBench experiments
(`scripts/drbench/experiments` in the core repository): page prompt v1 at
temperature 0; the judge asks both candidate orders with one output token and
`top_logprobs`, and averages them to cancel position bias
(`docstruct.fusion.judge`); the region prompt lists the providers' hypotheses;
the table prompt asks for one greedy and `samples` seeded generations
(`vlm_tables.py`).
The adapter returns this **evidence only**. Thresholds and the select, replace
or drop decisions belong to the agentic core.

Request `parameters`, as the core's clients send them:

| Capability | Parameter | Meaning |
|---|---|---|
| `document.ocr.page` | — | None used; the prompt is language-agnostic, so the client's `language` is ignored |
| `document.adjudicate.page` | `candidate_a`, `candidate_b` (required) | The two transcriptions; the output's `pB` is the debiased probability that `candidate_b` is more faithful |
| | `prompt` | Judge prompt variant: `base` (default), `para` (adds block structure) or `err` (counts errors) |
| `document.adjudicate.region` | `candidates` (required) | List of OCR hypotheses for the crop; empty strings are skipped |
| `table.recognize` | see below | |

Provider `config` keys:

| Key | Default | Meaning |
|---|---|---|
| `model` | — (required) | Model id the server serves |
| `api_key` | empty | Bearer token |
| `max_concurrency` | 4 | Requests in flight, shared by all instances of the provider in one process; match the server's parallel slots (divide them among workers if the Toolbox runs more than one). The shipped `qwen3-vl-8b` sets 2, see [Production server](#production-server) |
| `page_max_tokens` | 4096 | Output limit of a page transcription |
| `judge_max_chars` | 14000 | Each judge candidate is cut to this length; both plus the image must fit the server's context |
| `region_max_tokens`, `region_max_chars` | 1024, 1800 | Region output limit and per-hypothesis cut |
| `top_logprobs` | 20 | Log-probabilities requested for the judge token |
| `page_sampling` | `{}` | Extra sampling keys for page requests, in the server's own naming (`repeat_penalty` for llama-server, `repetition_penalty` for vLLM) |
| `table_max_tokens`, `table_hint_chars` | 6000, 6000 | Output limit of each table generation and per-hint cut |
| `table_max_samples` | 8 | Largest `samples` a request may ask for |
| `slot_wait_seconds` | `timeout_seconds` | How long a chat of the [VLM gateway](api.md#vlm-chat-gateway) waits for a slot before a 504 |

`table.recognize` exists to test the table specialist, which the Dr.DocBench
experiments rejected (TEDS 69.13 to 64.75 with Qwen3-VL-32B) without isolating
why. Its parameters expose the experiment's choices so they can be varied:

| Parameter | Default (as in the experiment) | Alternative to test |
|---|---|---|
| `hints` / `use_hints` | providers' tables shown | `use_hints: false` — no anchoring on provider errors |
| `header_cells` | `th` | `td` — header tag the evaluator may not normalize |
| `samples`, `temperature`, `top_p`, `seed` | 3, 0.7, 0.95, 0 | fewer samples, lower temperature |

Each generation reports its `finish` reason, so tables cut at the token limit
are visible. Samples are separate requests with consecutive seeds: reproducible
and cacheable, and independent of server support for `n > 1`. Selecting among
the generations and the providers' tables (the medoid by TEDS) is the caller's
step; a pool of one greedy plus several samples against two provider tables
favours the model's own cluster, which is one of the hypotheses to test.

The provider `version` names the served weights and is part of the cache key:
bump it when the server loads a different model. A request larger than the
server's context window fails with `400 invalid_input`; a server that returns no
log-probabilities, or none for the answers `1` and `2`, makes the judge fail
instead of guessing.

The experiments validated the judge and the page transcription on
Qwen3-VL-32B (FP8); the production server runs Qwen3-VL-8B (Q4_K_M) with 8192
tokens per slot. Results are expected to differ, so the core's thresholds must
be re-validated on this model before the methods that use these capabilities
are enabled.

### Production server

The production server is CPU-only (8 vCPUs, VM2) and runs as the systemd unit
`llama-server.service`, with the key in an `EnvironmentFile`
(`LLAMA_API_KEY`) instead of on the command line:

``` bash
llama-server -hf Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M --alias qwen3-vl-8b \
  --host <internal address> --port 81 \
  --parallel 2 --ctx-size 16384 --threads 8 --image-min-tokens 1024 --metrics
```

The unit is versioned in `deploy/vlm-gateway/llama-server.service`, which
moves the server to loopback behind the [VLM gateway](#vlm-gateway). The two
sides must change together:

- `max_concurrency` of `qwen3-vl-8b` in `providers-config.yaml` must equal
  `--parallel` in the one Toolbox that calls the server: the gateway, once it
  is deployed. With more requests than slots, the extra requests queue inside
  llama-server, where the Toolbox's timeouts cannot see them.
- `--ctx-size` is the **total** KV cache, divided among the slots: 16384 over 2
  slots gives 8192 tokens per slot, which `page_max_tokens`, `judge_max_chars`
  and `table_max_tokens` are sized for. More room per slot means a larger
  `--ctx-size` and more RAM, not fewer slots. On this host, a larger cache,
  or a second model loaded beside it, pushed the weights into swap and
  stalled every request.

The limiter is the queue. A capability request waits for a slot with no time
limit; a gateway chat waits up to `slot_wait_seconds` (1800 s). Either then
gets `timeout_seconds` (600 s) at the server.

On this host the server reads an image in about 33 s and generates 3 to 5
tokens/s (about 3 with both slots busy). The server sends nothing until the
answer is complete, so the Toolbox's client gives up on any request after
`timeout_seconds`: **no request holds a slot for more than about 600 s**, and
at 3 tokens/s that is about 1700 output tokens. The token budgets are bounded
by that, not only by their own value:

| Budget | Value | Time at 3 tokens/s (+ ~33 s for the image) |
|---|---|---|
| `page_max_tokens` | 2048 | ~12 min: a long page can time out (504) before it reaches `finish: "length"` |
| `table_max_tokens` | 3072 | ~17 min: a long table generation times out first |

So both slots free up at least once every ~600 s, and `slot_wait_seconds`
(1800 s) covers about three such turns. The wait can still run out when new
requests keep taking freed slots first, because the slot is not handed out in
arrival order. `table.recognize` makes 1 + `samples` sequential requests, each
taking a slot. A client that disconnects
does not free the slot until the server answers the request already running.

### VLM gateway

Staging and production share the model server. Each Toolbox limits only its
own requests, so two of them with `max_concurrency: 2` together sent the server
four requests for its two slots. The VLM gateway is one more Toolbox, in
gateway mode (`acessilia_toolbox.api.gateway_app:create_gateway_app`), on the
model server's host: the one client of llama-server, which moves to loopback.
Its limiter holds every environment to the server's slots, and nothing reaches
the server around it.

The environments' Toolboxes keep their `qwen3-vl-8b` provider and point it at
the gateway, which serves the same chat API under `/v1/vlm`:

| Setting | Environment Toolbox | Gateway |
|---|---|---|
| `VLM_SERVE_URL` / endpoint | `http://<gateway>:8002/v1/vlm` | `http://127.0.0.1:8080` |
| `VLM_API_KEY` | the gateway's `TOOLBOX_API_KEY` | llama-server's key |
| `VLM_MAX_CONCURRENCY` | 4: relay, do not queue | 2: `--parallel` |
| `VLM_TIMEOUT_SECONDS` | 2700: longer than the gateway's 1800 + 600 | 600 |
| `VLM_SLOT_WAIT_SECONDS` | 120: small, its worst case (120 + 2700) must fit the core's 3000 | 1800: the wait for one of the server's slots |

The provider's health check reaches `GET /v1/vlm/health` on the gateway.
`deploy/vlm-gateway/` has the compose file, the configuration, both systemd
units and the switch-over steps.

## Chemistry

``` text
chem.recognize : image -> reaction description + mhchem (docling-serve VLM)
chem.convert   : \ce{...} LaTeX -> structured reaction + pt-BR text (pure-python)
```

`chem.convert` takes the same `artifact/latex@1` payload as the math
capabilities, so the expression travels under `latex`:

``` json
{ "latex": "\\ce{CH4 + 2O2 -> CO2 + 2H2O}" }
```

`chem.recognize` uses docling-serve's VLM pipeline (Granite Vision) and
extracts `\ce{}` expressions from the model output. `chem.convert`
normalizes mhchem syntax (coefficients, charges, states of matter,
reaction arrows with conditions) into a structured form matching
`artifact/mhchem@1`, including a human-readable pt-BR rendering for
verbalization.

## Accessibility validators

Validation is valuable as deterministic feedback to agentic reflection
loops. A validator reports facts; the Agentic Core decides whether to
repair, retry, escalate, or request human review.

## Provider lifecycle

Starting/stopping heavy services may be exposed as an explicit
infrastructure capability or deployment concern. The Toolbox must not
autonomously unload a service when doing so would constitute an
application-level decision. Technical orchestration policies must be
explicit and observable.
