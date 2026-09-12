"""Thin adapters binding external providers to capability contracts."""

from collections.abc import Callable

from acessilia_toolbox.core.errors import ProviderNotFoundError
from acessilia_toolbox.core.provider import ProviderAdapter, ProviderDescriptor
from acessilia_toolbox.providers.docling import DoclingProvider
from acessilia_toolbox.providers.docling_layout import DoclingLayoutProvider
from acessilia_toolbox.providers.docling_math import DoclingMathProvider
from acessilia_toolbox.providers.pure_math import PureMathProvider

ADAPTERS: dict[str, Callable[[ProviderDescriptor], ProviderAdapter]] = {
    "docling": DoclingProvider,
    "docling-layout": DoclingLayoutProvider,
    "docling-math": DoclingMathProvider,
    "pure-math": PureMathProvider,
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
    "DoclingProvider",
    "PureMathProvider",
    "create_adapter",
]
