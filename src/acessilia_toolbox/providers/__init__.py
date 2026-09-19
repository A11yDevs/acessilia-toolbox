"""Thin adapters binding external providers to capability contracts."""

from collections.abc import Callable

from acessilia_toolbox.core.artifact import ArtifactStore, ExecutionCache
from acessilia_toolbox.core.errors import ProviderNotFoundError
from acessilia_toolbox.core.provider import ProviderAdapter, ProviderDescriptor
from acessilia_toolbox.providers.dataset import DatasetAdapter
from acessilia_toolbox.providers.dataset_github import GitHubDatasetProvider
from acessilia_toolbox.providers.dataset_huggingface import HuggingFaceDatasetProvider
from acessilia_toolbox.providers.docling import DoclingProvider
from acessilia_toolbox.providers.docling_chem import DoclingChemProvider
from acessilia_toolbox.providers.docling_layout import DoclingLayoutProvider
from acessilia_toolbox.providers.docling_math import DoclingMathProvider
from acessilia_toolbox.providers.docling_ocr import DoclingOcrProvider
from acessilia_toolbox.providers.mineru import MineruProvider
from acessilia_toolbox.providers.mineru_layout import MineruLayoutProvider
from acessilia_toolbox.providers.mineru_ocr import MineruOcrProvider
from acessilia_toolbox.providers.music_omr import AudiverisProvider, HomrProvider
from acessilia_toolbox.providers.pure_accessibility import PureAccessibilityProvider
from acessilia_toolbox.providers.pure_chem import PureChemProvider
from acessilia_toolbox.providers.pure_code import PureCodeProvider
from acessilia_toolbox.providers.pure_math import PureMathProvider
from acessilia_toolbox.providers.pure_text import PureTextProvider
from acessilia_toolbox.providers.pymupdf_pdf import PyMuPDFProvider
from acessilia_toolbox.providers.rapid_latex_ocr import RapidLatexOcrProvider

# Global store/cache references injected by app.py for dataset mirroring.
# These are set once at startup and read by the dataset adapter factories.
_dataset_store: ArtifactStore | None = None
_dataset_cache: ExecutionCache | None = None


def configure_dataset_mirroring(
    store: ArtifactStore | None = None,
    cache: ExecutionCache | None = None,
) -> None:
    """Inject artifact store and cache into dataset adapter factories.

    Called once during application startup.  Both are optional: without a
    store, mirroring is disabled; without a cache, metadata is never cached.
    """
    global _dataset_store, _dataset_cache
    _dataset_store = store
    _dataset_cache = cache


def _dataset_github_factory(descriptor: ProviderDescriptor) -> DatasetAdapter:
    return DatasetAdapter(
        descriptor,
        GitHubDatasetProvider(descriptor),
        store=_dataset_store,
        cache=_dataset_cache,
    )


def _dataset_huggingface_factory(descriptor: ProviderDescriptor) -> DatasetAdapter:
    return DatasetAdapter(
        descriptor,
        HuggingFaceDatasetProvider(descriptor),
        store=_dataset_store,
        cache=_dataset_cache,
    )


ADAPTERS: dict[str, Callable[[ProviderDescriptor], ProviderAdapter]] = {
    "audiveris": AudiverisProvider,
    "dataset-github": _dataset_github_factory,
    "dataset-huggingface": _dataset_huggingface_factory,
    "docling": DoclingProvider,
    "docling-chem": DoclingChemProvider,
    "docling-layout": DoclingLayoutProvider,
    "docling-math": DoclingMathProvider,
    "docling-ocr": DoclingOcrProvider,
    "homr": HomrProvider,
    "mineru": MineruProvider,
    "mineru-layout": MineruLayoutProvider,
    "mineru-ocr": MineruOcrProvider,
    "pure-accessibility": PureAccessibilityProvider,
    "pure-chem": PureChemProvider,
    "pure-code": PureCodeProvider,
    "pure-math": PureMathProvider,
    "pure-text": PureTextProvider,
    "pymupdf-pdf": PyMuPDFProvider,
    "rapid-latex-ocr": RapidLatexOcrProvider,
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
    "AudiverisProvider",
    "DatasetAdapter",
    "DoclingChemProvider",
    "DoclingLayoutProvider",
    "DoclingMathProvider",
    "DoclingOcrProvider",
    "DoclingProvider",
    "GitHubDatasetProvider",
    "HomrProvider",
    "HuggingFaceDatasetProvider",
    "MineruLayoutProvider",
    "MineruOcrProvider",
    "MineruProvider",
    "PureAccessibilityProvider",
    "PureChemProvider",
    "PureCodeProvider",
    "PureMathProvider",
    "PureTextProvider",
    "PyMuPDFProvider",
    "RapidLatexOcrProvider",
    "create_adapter",
]
