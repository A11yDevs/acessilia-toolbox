# TeleOCR: local evidence, integration and remaining work

The local experiments support offering TeleOCR as an optional extractor. They do
not establish a universally better provider or a reliable automatic selector.
The optional Toolbox HTTP adapter was integrated through PR #30 on 2026-10-06. This follow-up documents the completed experiments. The expanded 240-page comparison
is complete; live GPU service validation and external review remain pending.
Benchmark inference was run
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
- In the completed expansion, one short caption became 6,772 characters dominated
  by repeated tokens. The page completed after OOM retries at batches 8 and 3,
  succeeding at batch 1 in 2,150 seconds. The original output is retained; no
  threshold or frozen selector was adjusted from this observation. Its scored Text value is 0.40; the completed expansion below records its limits.

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

Before claiming live-service readiness, validate an actual endpoint,
including inference identity/cache behavior, timeouts and failure recovery.

## Validation and remaining experiments

- Unit and REST integration suites: **424 passed**, including the actual adapter,
  executor and normalization builder with simulated HTTP transport.
- Ruff over source/tests passed. Strict mypy passed 53 source files with a local-only
  override for absent optional `homr` imports; repository settings are unchanged.
- The full local test command produced 429 passed, 49 skipped, 9 errors and 1
  failure. The MinIO contract errors result from missing optional `boto3`, so live
  storage was not checked; the snapshot failure requires the
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

## Completed 240-page expansion (2026-10-05)

All 11 variants completed both native evaluators. The
[expanded aggregate artifact](experiments/teleocr-expanded-summary.json) includes
all component denominators, paired intervals, source/content strata, selector
cost accounting, dataset/model/evaluator revisions and artifact hashes.
Sampling and selectors remained frozen; the runtime protocol was amended after
observed failures. No held-out GT was used to choose providers or repair content.

Dr: 120 additional pages across 66 known books, 115 scorable; 115 text, 111 RO,
5 baseline/Tele table pages and 4 formula pages. These books overlap earlier runs.
GT has 16 tables on 6 pages. Baseline/Tele/hybrid component masks coincide, but
all omit one GT table page from native TEDS; Docling has no Dr TEDS records in
the deployed transport. Missing table predictions can be omitted rather than
penalized, so retain the coverage caveat when ranking providers.

Omni: 120 English pages, balanced 40 tables / 40 formula-without-table / 40 other;
119 scorable, 108 text, 119 RO, 40 table and 42 formula pages. One header-only
page is outside scored categories. All provider component masks coincide here.
This is a diagnostic sample, not a full-benchmark estimate. Filename-inferred
groups do not prove independent/unseen books; model-training overlap is unknown.

**The Overall column below is derived per page from available text/RO/TEDS,
excluding formulas without CDM. It is not an official Omni headline, EvalAI or
leaderboard score. Table averages below weight pages; native table-weighted
TEDS is reported separately. Do not pool the two benchmarks.**

### drdocbench

| Variant | Derived Overall | Text | RO | Page-weighted TEDS | Formula 1−ED |
|---|---:|---:|---:|---:|---:|
| base | 76.79 | 80.60 | 73.16 | 49.39 | 1.06 |
| xycut | 77.96 | 80.62 | 75.57 | 49.39 | 1.06 |
| tele_raw | 69.54 | 79.71 | 59.15 | 71.38 | 22.78 |
| tele_decor | 79.51 | 79.70 | 79.72 | 71.38 | 22.78 |
| pre | 82.24 | 83.05 | 81.15 | 71.38 | 22.78 |
| post | 80.49 | 82.63 | 78.72 | 71.38 | 22.78 |
| zero_text_guard | 82.32 | 82.44 | 81.95 | 71.38 | 22.78 |
| doctele | 83.51 | 84.62 | 82.14 | 71.38 | 8.09 |
| docteleform | 83.51 | 84.62 | 82.14 | 71.38 | 22.78 |
| docling | 64.47 | 75.92 | 51.64 | — | 1.06 |
| mineru | 56.54 | 64.99 | 48.09 | 49.39 | 0.00 |

### omnidocbench

| Variant | Derived Overall | Text | RO | Page-weighted TEDS | Formula 1−ED |
|---|---:|---:|---:|---:|---:|
| base | 78.46 | 89.93 | 69.73 | 77.21 | 17.76 |
| xycut | 78.61 | 89.96 | 70.02 | 77.21 | 17.76 |
| tele_raw | 92.00 | 96.83 | 85.56 | 96.32 | 93.18 |
| tele_decor | 91.85 | 96.83 | 85.25 | 96.32 | 93.18 |
| pre | 90.22 | 95.23 | 83.36 | 95.48 | 90.79 |
| post | 89.47 | 95.32 | 82.71 | 96.32 | 88.74 |
| zero_text_guard | 92.49 | 98.17 | 85.55 | 96.49 | 93.18 |
| doctele | 86.73 | 93.08 | 77.74 | 96.32 | 65.29 |
| docteleform | 88.33 | 92.68 | 81.37 | 96.32 | 93.03 |
| docling | 67.18 | 89.07 | 65.29 | 0.00 | 24.28 |
| mineru | 73.66 | 83.81 | 64.09 | 77.21 | 15.47 |


### Findings that affect provider and policy choice

- Raw Tele on Omni: Text +6.91, RO +15.83, formula 1−ED +75.42 versus baseline.
  Derived Overall +13.55, source-group 95% interval [10.62, 16.61], 105 gains /
  4 losses / 10 ties on 119 pages. Both runtime failures remain in the scores.
- Native Omni TEDS weights 100 table records: baseline 53.21, raw/decorative
  Tele 71.08. Page-weighted Tele TEDS is 96.32 on 40 pages. The failed dense
  newspaper alone contributes 26 zero table records; these denominators explain
  why the page average cannot replace the native score.
- Dr decorative Tele: Overall +2.72, interval [-3.88, 9.86], 65/29/21; Text
  -0.90 and RO +6.56. Docling/Tele improves Dr Overall +6.72 [2.45, 11.50],
  57/32/26; Text +4.02 and RO +8.98. These are known-book page diagnostics.
- Fusion does not transfer universally: on Omni generic Docling/Tele loses
  5.12 Overall versus decorative Tele [−7.15, −3.15], with 5/63/51. Formula
  1−ED falls 93.18 → 65.29 (−27.89, 37 losses / 0 gains / 5 ties).
  Formula-preserving fusion restores 93.03 (−0.15, 3 losses / 0 gains / 39 ties),
  but still loses 3.52 Overall [−5.34, −1.79]. In Dr it restores 8.09 → 22.78
  on four formula pages. This is a research policy, not a shipped default.
- Decorative-last rendering helps Dr but loses 0.31 RO on Omni versus raw Tele:
  1 gain / 2 losses / 116 ties, interval [−0.93, 0.07]. A visually checked
  textbook section heading was labeled `header`; moving it to the end changes
  RO from 100 to 66.67 while text stays 100. Keep benchmark-specific rendering
  separate from the canonical tree and optional.
- Frozen PRE/POST selectors lose Omni Overall against decorative Tele:
  PRE −1.63 [−3.17, −0.42], 0/9/110; POST −2.38 [−4.72, −0.58], 0/7/112.
  Dr increments +2.74 and +0.98 have intervals including zero. No default
  automatic router is justified. PRE would avoid 17/120 Dr and 10/120 Omni
  page extractions, but those are simulated savings, not measured speed gains.
- Frozen empty-content fallback is the more limited useful option: Dr +2.82
  [0, 6.30], 4/0/111; Omni +0.64 [0, 1.63], 2/0/117, versus decorative Tele.
  It uses the baseline only when all Tele content is empty and baseline text
  exists. It still requires every Tele call and misses partial omissions. The
  six observed gains do not establish universal safety; keep it an explicit
  orchestration policy evaluated on fresh data, outside this transport adapter.

Intervals use 3,000 whole-source-group resamples; ties use 0.05 points. Comparisons
and strata are exploratory, without adjustment for multiple comparisons. The
aggregate artifact contains complete intervals and coverage for all variants.

### Where the alternatives gain or lose

These strata are descriptive diagnostics using benchmark labels, never routing
features. Source rows below require at least five scorable pages solely for
display; all smaller strata remain in the aggregate artifact. Small strata do
not support production thresholds. Formula-stratum Overall still excludes formulas.

| Sample / stratum | Overall n | Δ raw Tele | Δ decorative Tele | Δ Docling/Tele | Δ formula-preserving hybrid |
|---|---:|---:|---:|---:|---:|
| drdocbench / source:DESIGN | 6 | 4.68 | 7.06 | 3.97 | 3.97 |
| drdocbench / source:EDUCATION | 8 | -4.80 | 18.10 | 18.08 | 18.08 |
| drdocbench / source:GAMES&ACTIVITIES | 5 | 4.29 | 8.67 | 12.75 | 12.75 |
| drdocbench / source:HOUSE&HOME | 5 | -3.03 | 3.36 | 5.66 | 5.66 |
| drdocbench / source:JUVENILENONFICTION | 5 | 2.76 | 2.76 | -1.35 | -1.35 |
| drdocbench / source:SOCIALSCIENCE | 6 | -4.30 | 5.43 | 12.22 | 12.22 |
| drdocbench / source:SPORTS&RECREATION | 6 | -11.13 | -11.13 | 14.86 | 14.86 |
| drdocbench / source:YOUNGADULTFICTION | 5 | -42.16 | -37.27 | -0.58 | -0.58 |
| omnidocbench / content:formula | 40 | 22.98 | 23.03 | 15.18 | 19.67 |
| omnidocbench / content:other | 39 | 7.80 | 7.28 | 1.81 | 1.81 |
| omnidocbench / content:table | 40 | 9.72 | 9.72 | 7.68 | 7.93 |
| omnidocbench / source:PPT2PDF | 21 | 19.59 | 19.59 | 14.76 | 14.85 |
| omnidocbench / source:academic_literature | 21 | 18.53 | 18.53 | 13.02 | 16.61 |
| omnidocbench / source:book | 22 | 11.64 | 11.64 | 6.27 | 9.12 |
| omnidocbench / source:colorful_textbook | 14 | 12.03 | 10.84 | 5.64 | 6.96 |
| omnidocbench / source:exam_paper | 20 | 15.79 | 15.90 | 5.27 | 6.83 |
| omnidocbench / source:magazine | 8 | 5.87 | 5.87 | 5.58 | 5.58 |
| omnidocbench / source:newspaper | 12 | 1.99 | 1.69 | 2.87 | 2.87 |

### Runtime and qualitative limits

238/240 model extractions completed. Two dense newspaper pages exceeded the
amended 90-minute active wall budget and remain explicit empty predictions.
After OOM/stalled generation, the 78 pending pages used fresh unchanged CLI
processes, including startup in the budget. This amendment was ex post, adds
startup cost and provides no measured acceleration. Computer shutdowns were
audited separately from model failures. On the final interrupted page, a
retrospectively disclosed conservative 3,600-second allocation left 1,800 seconds;
elapsed budget was then persisted every 30 seconds. Computer-off downtime was
excluded. Cached successful responses and all selected image bytes were hash-checked.

Visual checks confirm complete omission of small captions and comic speech text
when regions are returned only as images; partial omission of labels inside
screenshots is not caught by the empty-content guard. Hybrid fusion can restore
text while worsening order. A repeated-caption case scores only 0.40 in Text
despite a valid response schema; its derived Overall is 50.20 versus baseline
52.85. The unmodified response took 2,150 seconds. A valid adapter contract is
not a text-quality guarantee; no output was cleaned or threshold refitted here.

### Integration decision

The integrated optional HTTP adapter remains separate from provider routing. The
Agentic Core can explicitly choose Tele, the existing baseline, or experimental
fusion policies. This documentation follow-up does not ship the research selectors, benchmark
renderers, formula monkeypatch or repetition filter. Defaults remain unchanged.
Fresh-data follow-ups: region fallback with provenance/geometry checks, bounded
generation with actual remote cancellation and partial checkpoints, repetition
detection, and formula-preserving fusion with CDM. The live GPU HTTP service and
external review of the expanded evidence remain outstanding. The earlier 424 local unit/REST checks used adapter revision adff307 and MockTransport. Maintainer revision 98f00cd passed remote Python 3.12 CI and was merged into develop through PR #30 by a maintainer. The experiment outputs were not recalculated with that later code revision. This follow-up changes documentation only. No merge, deployment, message or EvalAI submission was performed by this experiment agent.
