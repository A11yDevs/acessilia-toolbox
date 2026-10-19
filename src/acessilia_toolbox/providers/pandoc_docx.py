"""Pandoc DOCX export provider adapter.

Thin client of the ``/render/docx`` route of the ``pdf-ua-render`` sidecar,
which runs the same pinned Pandoc as the PDF/UA export (see ``pandoc_sidecar``).
DOCX needs no TeX, so only the sidecar and Pandoc versions enter the cache key.
"""

from __future__ import annotations

from acessilia_toolbox.providers.pandoc_sidecar import PandocSidecarProvider

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class PandocDocxProvider(PandocSidecarProvider):
    """Calls the Pandoc sidecar to turn a Pandoc JSON AST into DOCX."""

    LABEL = "DOCX"
    RENDER_PATH = "/render/docx"
    MEDIA_TYPE = DOCX_MEDIA_TYPE
    SUFFIX = "docx"
    # A DOCX file is a ZIP archive.
    MAGIC = b"PK\x03\x04"
    VERSION_KEYS = ("version", "pandoc")
