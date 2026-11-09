# MinerU 4 fixtures

`middle_json_v4.json` is a hand-built, schema-validated example of the
`docvortex.middle` protocol 2.0 emitted by MinerU 4.0.12 (DocVortex 0.4.19).
It is intentionally small but exercises the block shapes the adapter must
support: `doc_title`/`paragraph_title`, inline equation/code/hyperlink spans,
nested list children, `table_body` HTML, a standalone equation, visual captions
and code bodies.

`middle-2.0.schema.json` is copied verbatim from DocVortex v0.4.19,
`schemas/middle-2.0.json` (MIT):

- Source: https://github.com/myhloli/DocVortex/blob/v0.4.19/schemas/middle-2.0.json
- SHA-256: `02e613fd61e8aaec8afa57b98a34be513b32add53d281bbd04658e3bb96a562f`

`tests/unit/test_mineru_provider.py` validates the fixture against this schema
so an invalid protocol example fails CI instead of hiding content-loss bugs.
