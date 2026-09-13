"""Thin adapters binding external providers to capability contracts."""

from collections.abc import Callable

from acessilia_toolbox.core.errors import ProviderNotFoundError
from acessilia_toolbox.core.provider import ProviderAdapter, ProviderDescriptor
from acessilia_toolbox.providers.docling import DoclingProvider
from acessilia_toolbox.providers.docling_layout import DoclingLayoutProvider
from acessilia_toolbox.providers.docling_math import DoclingMathProvider
from acessilia_toolbox.providers.docling_ocr import DoclingOcrProvider
from acessilia_toolbox.providers.pure_accessibility import PureAccessibilityProvider
from acessilia_toolbox.providers.pure_code import PureCodeProvider
from acessilia_toolbox.providers.pure_math import PureMathProvider
from acessilia_toolbox.providers.pure_text import PureTextProvider
from acessilia_toolbox.providers.pymupdf_pdf import PyMuPDFProvider

ADAPTERS: dict[str, Callable[[ProviderDescriptor], ProviderAdapter]] = {
    "docling": DoclingProvider,
    "docling-layout": DoclingLayoutProvider,
    "docling-math": DoclingMathProvider,
    "docling-ocr": DoclingOcrProvider,
    "pure-accessibility": PureAccessibilityProvider,
    "pure-code": PureCodeProvider,
    "pure-math": PureMathProvider,
    "pure-text": PureTextProvider,
    "pymupdf-pdf": PyMuPDFProvider,
}


def create_adapter(descriptor: ProviderDescriptor) -> ProviderAdapter:
    """Instantiate the adapter registered for a provider."""
    try:
        adapter = ADAPTERS[descriptor.id]
    except KeyError:
        raise ProviderNotFoundError(
            f"no adapter implements provider {descriptor.id}", provider=descriptor.id
        ) from None
    return adapter(descriptor)


__all__ = [
    "ADAPTERS",
    "DoclingLayoutProvider",
    "DoclingMathProvider",
    "DoclingOcrProvider",
    "DoclingProvider",
    "PureAccessibilityProvider",
    "PureCodeProvider",
    "PureMathProvider",
    "PureTextProvider",
    "PyMuPDFProvider",
    "create_adapter",
]
