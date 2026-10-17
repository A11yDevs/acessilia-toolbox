# PDF/UA validation

``` text
pdf.validate.pdf_ua : PDF -> PDF/UA-1 (or PDF/UA-2) validation report
```

The `verapdf` provider calls the `verapdf-validate` sidecar
(`docker/verapdf-validate.Dockerfile`), which runs a pinned veraPDF CLI on a JRE
and reduces its report to `artifact/pdf-validation-report@1`. Configure the
endpoint with `VERAPDF_VALIDATE_URL`.

It complements [PDF/UA export](pdf-ua-export.md): `document.export.pdf_ua`
produces a tagged PDF, this capability checks it. Any PDF can be validated,
whatever produced it.

## Request

``` bash
curl -H "Authorization: Bearer $TOOLBOX_API_KEY" \
  -F file=@document.pdf\;type=application/pdf \
  -F 'parameters={"profile":"ua1"}' \
  http://localhost:8002/v1/capabilities/pdf.validate.pdf_ua:execute
```

| Parameter | Default | Meaning |
|---|---|---|
| `profile` | `ua1` | veraPDF validation profile: `ua1` (ISO 14289-1) or `ua2` (ISO 14289-2) |

To validate a PDF that `document.export.pdf_ua` stored, download it first with
`GET /v1/artifacts/{artifact_id}`.

## Response

A non-compliant PDF is a **successful** result (`200`): `compliant` is `false`
and `failures` lists each failed rule. Only a file veraPDF cannot read (not a
PDF, damaged, encrypted) is an error: the sidecar answers `422`, which the API
reports as `400`.

``` json
{
  "compliant": false,
  "profile": "ua1",
  "profile_name": "PDF/UA-1 validation profile",
  "statement": "PDF file is not compliant with Validation Profile requirements.",
  "summary": {"passed_rules": 100, "failed_rules": 6, "passed_checks": 29, "failed_checks": 8},
  "failures": [
    {
      "specification": "ISO 14289-1:2014",
      "clause": "7.1",
      "test_number": 11,
      "description": "The logical structure of the conforming file shall be described by a structure hierarchy rooted in the StructTreeRoot entry …",
      "object": "PDDocument",
      "test": "containsStructTreeRoot == true",
      "tags": ["structure"],
      "failed_checks": 1,
      "checks": [{"context": "root/document[0]", "message": "StructTreeRoot entry is not present in the document catalog"}]
    }
  ],
  "validator": {"name": "veraPDF", "version": "1.0.0", "verapdf": "1.30.2"}
}
```

`failed_checks` is the true number of failing objects for a rule; `checks`
lists only the first `VERAPDF_MAX_CHECKS_PER_RULE` (default 5), so a badly
tagged book does not produce a multi-megabyte report.

veraPDF checks the machine-verifiable part of PDF/UA. Requirements that need
human judgement (whether alt text is meaningful, whether the reading order
makes sense) are outside any automated validator; see the Matterhorn Protocol
for the split.

## Caching

The cache key covers the PDF bytes, the profile, the sidecar version and the
veraPDF release reported by `/version`. Validating the same PDF again returns
the stored report; upgrading veraPDF re-validates.

## Build

``` bash
docker build -f docker/verapdf-validate.Dockerfile -t verapdf-validate:latest .
```

The installer is checked against `VERAPDF_SHA256`, and the build validates an
untagged smoke-test PDF through the server's own code, so a broken JRE or
veraPDF install fails the build rather than the first request. Upgrade by
changing `VERAPDF_VERSION` and `VERAPDF_SHA256` together.

| Variable | Default | Meaning |
|---|---|---|
| `VERAPDF_TIMEOUT_SECONDS` | `240` | Per-file validation limit; the sidecar answers `504` past it |
| `VERAPDF_MAX_PDF_BYTES` | 200 MiB | Largest accepted PDF |
| `VERAPDF_MAX_CHECKS_PER_RULE` | `5` | Failing contexts listed per rule |
| `JAVA_OPTS` | `-Xmx1g` | JVM options; raise the heap for very large PDFs |

## Tests

`tests/unit/test_pdf_ua_validation.py` covers the adapter, the executor and the
report normalization against real veraPDF 1.30.2 reports in
`tests/fixtures/verapdf/`. `tests/integration/test_verapdf_toolchain.py` runs
the real CLI (set `VERAPDF_BIN` when it is not on PATH) and, when pandoc and
lualatex are installed, checks that a PDF rendered by `pdf-ua-render` passes
PDF/UA-1.
