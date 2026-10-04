# TeleOCR: local evidence, integration and remaining work

The local experiments support offering TeleOCR as an optional extractor. They do
not establish a universally better provider or a reliable automatic selector.
This PR implements the Toolbox HTTP adapter; GPU service deployment and the
expanded 240-page comparison are still pending. Benchmark inference was run
locally, independently of the proposed HTTP endpoint.

## Completed evidence

The official DrDocBench md2md evaluator was used with one-page windows and no
CDM. All predictions were present. **Overall here is a derived mean per page of
available text, reading-order and TEDS scores**, not an EvalAI submission score.
Formula edit scores are reported separately. The
[aggregate evidence](experiments/teleocr-local-summary.json) retains denominators,
paired comparisons, uncertainty, dataset/model revisions and source-artifact
hashes. It contains no benchmark images, annotations or extracted text.

### Development: 120 pages, 119 scorable

Docling/MinerU v13 scored 72.27; TeleOCR with decorative blocks moved to the end
scored 79.30; Docling/TeleOCR scored 80.00. These pages were previously explored.
Table coverage differs across providers, so these Overall values are descriptive
development results, not proof of a general improvement.

### Held-out books: 60 pages, 59 scorable, 16 books

Books were absent from the development sample. Sampling and selector parameters
were fixed before evaluating this sample. A page with empty GT is not scorable.
Model-training overlap with these public benchmarks is unknown.

| Variant | Overall | Text | Reading order | Overall delta vs baseline | Improved / regressed / tied |
|---|---:|---:|---:|---:|---|
| Docling/MinerU v13 | 73.13 | 73.29 | 72.97 | — | — |
| TeleOCR, decorative-last rendering | 78.03 | 76.27 | 79.78 | +4.90 | 31 / 23 / 5 |
| Docling/TeleOCR v13 | 83.15 | 83.65 | 82.65 | +10.02 | 28 / 23 / 8 |
| Frozen pre-inference selector | 81.10 | 79.46 | 82.74 | +7.97 | 26 / 18 / 15 |
| Frozen post-inference selector | 77.96 | 76.50 | 79.42 | +4.83 | 27 / 14 / 18 |

TeleOCR gained 2.99 text points and 6.82 reading-order points on average. The hybrid
gained 10.36 and 9.68 respectively. However, whole-book 95% bootstrap intervals for
Overall deltas include zero: TeleOCR **[-9.95, +22.59]**; hybrid **[-0.27, +22.37]**.
Some gains are concentrated in books where the baseline has grouping or false-table
errors. No page in this sample has annotated tables or formulas, so it cannot
validate either component.

Raw TeleOCR scored 70.02 on the same sample. The change to 78.03 after moving
headers, footers and page numbers to the end is a benchmark rendering convention,
not a different OCR model. This convention does not belong in the canonical tree.
Absolute scores from differently rendered development baselines are not pooled.

## Selection and fusion limitations

The frozen preselector gained 3.07 over TeleOCR, but its interval includes zero
and its gain is concentrated in one comic book. Excluding that book yields -0.19
on the other 55 scorable pages. The postselector changed the mean by -0.07.
Neither demonstrates a reliable held-out advantage over TeleOCR alone.

Only selection **before inference** can avoid GPU calls. All research pages were
actually extracted; avoided calls are simulated, not measured latency savings.
Post-inference selection and empty-output fallback still require TeleOCR inference.

Generic fusion can damage formula content. On 20 common development pages,
Docling/TeleOCR lost 13.48 formula 1-minus-edit points relative to TeleOCR
(book interval [-28.58, -0.40]; 9 losses, 0 gains, 11 ties). An experimental policy
preserving TeleOCR formulas remained almost unchanged (-0.03). CDM was not run;
this does not establish mathematical equivalence or full formula accuracy.

## Observed failures

- Comic speech balloons can be omitted when the model returns image regions
  without textual content; another provider can retain that text.
- Verses, lists and dispersed short text can be fragmented relative to benchmark
  grouping. Conversely, hybrid fusion can over-join catalog units.
- A rotated English marginal note was hallucinated as Korean text. A high page
  score did not guarantee that every extracted region was correct.
- Rotated two-page spreads can produce duplicate decorative blocks during fusion;
  the exact orientation cause still needs isolation.
- In the ongoing expansion, one short caption became 6,772 characters dominated
  by repeated tokens. The page completed after OOM retries at batches 8 and 3,
  succeeding at batch 1 in 2,150 seconds. The original output is retained; no
  threshold or frozen selector was adjusted from this observation. Its benchmark
  impact is not yet scored.

That last response passes schema and geometry validation. The adapter validates
the transport contract, **not the truth of the text**. Runtime budgets, cancellation
and repetition checks are future service/policy work, not implemented quality
guarantees. An HTTP client timeout alone does not guarantee cancellation of GPU work.

## Evaluator coverage requires explicit review

The pinned DrDocBench md2md implementation matches tables only when GT and
prediction contain tables of the same format. Missing predicted tables can leave
TEDS absent rather than zero, changing the components included in per-page Overall.
Across providers, this can confound aggregate rankings. In the additional 120 Dr
pages, GT has 16 tables on 6 pages; the existing baseline scores 15 tables on 5
pages, while deployed Docling transport exposes no table structure for TEDS.
The baseline/XY-cut component masks match, preserving their paired comparison.
Official outputs are retained, with coverage and common-component subset
diagnostics reported separately. A subset diagnostic is not a full-sample result.

## Optional Toolbox integration

The [adapter guide](teleocr.md) documents explicit `provider=teleocr` selection,
configuration and the `/health`, `/version`, `/predict` backend contract. The
Toolbox retains original geometry, source labels, crop rotation, formulas/HTML,
model identity and provenance. Missing calibrated confidence remains null.
GPU inference stays in a separate service; the Agentic Core chooses providers and
fusion policies. The core gains no ML runtime dependency or automatic semantic
routing. An example profile is provided; the default deployment is unchanged.

Before merge readiness, review the service contract and validate a live endpoint,
including inference identity/cache behavior, timeouts and failure recovery.

## Validation and remaining experiments

- Unit and REST integration suites: **424 passed**, including the actual adapter,
  executor and normalization builder with simulated HTTP transport.
- Ruff over source/tests passed. Strict mypy passed 53 source files with a local-only
  override for absent optional `homr` imports; repository settings are unchanged.
- The full local test command produced 429 passed, 49 skipped, 9 errors and 1
  failure. The errors require configured MinIO; the snapshot failure requires the
  dataset fixtures. These environments were not available. This is not a claim
  that live contract/snapshot tests passed. CI status must be checked separately.

The expanded sample has 120 additional DrDocBench pages and 120 English
OmniDocBench pages, with 40 table pages, 40 formula-without-table pages and 40 other
pages. Sampling was frozen before scores and selectors remain unchanged. Dr books
overlap earlier samples: this is page holdout, not book holdout. Omni groups are
inferred from filenames and its balanced diagnostic sample is not the complete
benchmark. Each uses its native evaluator; cross-benchmark scores are not pooled.

Candidate improvements for fresh-data tests are explicit empty-output fallback,
conservative repetition detection/retry, formula-preserving fusion, orientation
checks before box matching, and regional selection with provider provenance.
They must be measured separately, without fitting thresholds on the held-out pages.
No EvalAI submission or GPU HTTP deployment was made in this work.

Developed with assistance from Codex for implementation, experiment automation and
documentation. Reported numbers come from evaluator artifacts; execution and
visual checks were performed in the same work session, with external review pending.
