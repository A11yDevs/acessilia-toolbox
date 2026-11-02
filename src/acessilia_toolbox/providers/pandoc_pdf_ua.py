"""Pandoc PDF/UA export provider adapter.

Thin client of the ``/render`` route of the ``pdf-ua-render`` sidecar, which
owns the pinned Pandoc and TeX Live toolchain (see ``pandoc_sidecar``).
"""

from __future__ import annotations

from typing import Any

from acessilia_toolbox.providers.pandoc_sidecar import PandocSidecarProvider

PDF_MEDIA_TYPE = "application/pdf"
PDF_STANDARD = "ua-1"


class PandocPdfUaProvider(PandocSidecarProvider):
    """Calls the pdf-ua-render sidecar to turn a Pandoc JSON AST into PDF/UA."""

    LABEL = "PDF/UA"
    RENDER_PATH = "/render"
    MEDIA_TYPE = PDF_MEDIA_TYPE
    SUFFIX = "pdf"
    MAGIC = b"%PDF-"
    # Everything the sidecar reports that shapes the PDF bytes. All of it enters
    # the cache key, so a TeX Live, Pandoc or template change never reuses an old PDF.
    VERSION_KEYS = (
        "version",
        "pandoc",
        "engine",
        "engine_version",
        "latex_kernel",
        "template_sha256",
    )

    def _document(self, content: bytes, toolchain: dict[str, str]) -> dict[str, Any]:
        return {
            **super()._document(content, toolchain),
            "pdf_standard": PDF_STANDARD,
            "engine": toolchain.get("engine", ""),
        }
