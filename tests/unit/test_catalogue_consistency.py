"""Keeps the shipped catalogue, provider topology and adapters consistent.

A capability is only real when all three layers agree: the manifest in
``capabilities/``, the provider entry in ``providers-config.yaml`` and a Python
adapter registered in ``ADAPTERS``. Drift between them produced capabilities the
gateway advertised but could not execute, and providers bound to capabilities
that no manifest exposed.

These tests encode the invariants so the three layers cannot drift again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import ProviderNotFoundError
from acessilia_toolbox.core.provider import ProviderRegistry
from acessilia_toolbox.providers import ADAPTERS, create_adapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_DIR = PROJECT_ROOT / "capabilities"
PROVIDERS_CONFIG = PROJECT_ROOT / "providers-config.yaml"
SCHEMAS_DIR = PROJECT_ROOT / "schemas"

# Transport-level capabilities the provider registry tracks but that are not
# public capabilities: the gateway uses them internally. They are also the only
# providers that are not built through ADAPTERS -- the artifact store and cache
# are constructed directly by api/app.py.
INTERNAL_CAPABILITIES = frozenset({"cache.get", "cache.put"})
PLATFORM_PROVIDERS = frozenset({"minio", "filesystem", "valkey"})

# Bindings documented as opt-in profiles: the provider adapter exists and the
# shipped image serves the capability through another entry, but the provider is
# not registered in the default providers-config.yaml on purpose. The profile
# that enables them ships next to the config.
OPT_IN_BINDINGS = frozenset({("document.structure.extract", "teleocr")})


@pytest.fixture(scope="module")
def capabilities() -> CapabilityRegistry:
    return CapabilityRegistry.from_directory(CAPABILITIES_DIR)


@pytest.fixture(scope="module")
def providers() -> ProviderRegistry:
    # The shipped config resolves ${VAR} placeholders; provide values for every
    # referenced variable so the registry loads the real topology. The patch is
    # scoped to loading -- descriptors keep the resolved values afterwards -- so
    # these variables never reach other suites. ``tests/contract`` reads the same
    # names straight from the environment to build its own descriptors, and a
    # leaked placeholder value makes botocore reject the endpoint.
    with pytest.MonkeyPatch.context() as patch:
        for name in (
            "DOCLING_SERVE_URL",
            "MINERU_SERVE_URL",
            "NOUGAT_SERVE_URL",
            "AUDIVERIS_SERVE_URL",
            "RAPID_LATEX_OCR_URL",
            "PP_FORMULANET_URL",
            "RAPIDOCR_ORIENT_URL",
            "VLM_SERVE_URL",
            "MINIO_URL",
            "MINIO_ACCESS_KEY",
            "MINIO_SECRET_KEY",
            "VALKEY_URL",
        ):
            patch.setenv(name, f"http://{name.lower()}")
        return ProviderRegistry.from_file(PROVIDERS_CONFIG)


def test_every_capability_has_at_least_one_provider(
    capabilities: CapabilityRegistry, providers: ProviderRegistry
) -> None:
    unbound = [
        manifest.id
        for manifest in capabilities.manifests()
        if not providers.for_capability(manifest.id)
    ]
    assert not unbound, f"capabilities without a provider: {unbound}"


def test_every_provider_capability_has_a_manifest(
    capabilities: CapabilityRegistry, providers: ProviderRegistry
) -> None:
    known = set(capabilities.ids()) | INTERNAL_CAPABILITIES
    unknown = sorted(
        {
            capability
            for descriptor in providers.descriptors()
            for capability in descriptor.capabilities
            if capability not in known
        }
    )
    assert not unknown, (
        "providers declare capabilities with no manifest: "
        f"{unknown} (add a manifest or remove the declaration)"
    )


def test_every_provider_has_an_adapter(providers: ProviderRegistry) -> None:
    missing = [
        descriptor.id
        for descriptor in providers.descriptors()
        if descriptor.id not in ADAPTERS and descriptor.id not in PLATFORM_PROVIDERS
    ]
    assert not missing, (
        f"providers without an adapter in ADAPTERS: {missing} "
        "(every registered provider must be instantiable)"
    )


def test_every_adapter_is_instantiable(providers: ProviderRegistry) -> None:
    """Presence in ADAPTERS is not enough: the factory must actually build.

    A provider whose adapter raises on construction is still advertised by
    ``GET /v1/providers`` and only fails once a request reaches it.
    """
    failures = []
    for descriptor in providers.descriptors():
        if descriptor.id not in ADAPTERS:
            continue  # platform providers are constructed by api/app.py
        try:
            create_adapter(descriptor)
        except Exception as exc:
            failures.append((descriptor.id, f"{type(exc).__name__}: {exc}"))
    assert not failures, f"adapters that cannot be instantiated: {failures}"


def test_manifest_provider_bindings_are_registered(
    capabilities: CapabilityRegistry, providers: ProviderRegistry
) -> None:
    registered = {descriptor.id for descriptor in providers.descriptors()}
    dangling = [
        (manifest.id, binding.id)
        for manifest in capabilities.manifests()
        for binding in manifest.providers
        if binding.id not in registered
        and (manifest.id, binding.id) not in OPT_IN_BINDINGS
    ]
    assert not dangling, (
        f"manifest bindings pointing at unregistered providers: {dangling}"
    )


def test_manifest_bindings_name_an_existing_adapter(
    capabilities: CapabilityRegistry,
) -> None:
    """A binding must name an adapter that exists, opt-in or not."""
    unknown = [
        (manifest.id, binding.id)
        for manifest in capabilities.manifests()
        for binding in manifest.providers
        if binding.id not in ADAPTERS and binding.id not in PLATFORM_PROVIDERS
    ]
    assert not unknown, (
        f"manifest bindings naming no adapter: {unknown} "
        "(fix the manifest; do not leave a capability bound to a provider that "
        "cannot be instantiated)"
    )


def test_provider_records_the_capability_the_manifest_claims(
    capabilities: CapabilityRegistry, providers: ProviderRegistry
) -> None:
    """A binding must be mutual: manifest -> provider and provider -> manifest."""
    asymmetric = []
    for manifest in capabilities.manifests():
        for binding in manifest.providers:
            try:
                descriptor = providers.get(binding.id)
            except ProviderNotFoundError:
                continue  # opt-in profile, covered by the test above
            if not descriptor.implements(manifest.id):
                asymmetric.append((binding.id, manifest.id))
    assert not asymmetric, (
        f"providers bound by a manifest without declaring the capability: "
        f"{asymmetric}"
    )


def test_math_recognize_providers_conform_to_declared_schema(
    capabilities: CapabilityRegistry, providers: ProviderRegistry
) -> None:
    """Every provider bound to math.recognize must return a document conforming to artifact/latex@1.

    Guards against contract drift (issue #90) where providers return divergent shapes.
    """
    import httpx

    manifest = capabilities.get("math.recognize")
    schema_ref = manifest.output.schema_ref
    name, _, version = schema_ref.partition("@")
    schema_file = SCHEMAS_DIR / f"{name.split('/')[-1]}@{version}.json"
    schema = json.loads(schema_file.read_text(encoding="utf-8"))

    # Simulated responses representing successful extractions from each sidecar backend:
    simulated_responses: dict[str, dict[str, Any]] = {
        "rapid-latex-ocr": {
            "latex": r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
            "confidence": 0.98,
            "bbox": [10, 20, 210, 50],
        },
        "pp-formulanet": {
            "latex": r"A=\begin{pmatrix}a&b\\c&d\end{pmatrix}",
            "bbox": [10, 20, 210, 50],
        },
        "docling-math": {
            "document": {
                "json_content": {
                    "groups": [
                        {
                            "label": {"value": "formula"},
                            "text": r"E = mc^2",
                            "confidence": 0.95,
                            "prov": [
                                {
                                    "page_no": 2,
                                    "bbox": {
                                        "l": 50,
                                        "t": 100,
                                        "r": 500,
                                        "b": 50,
                                        "coord_origin": "BOTTOMLEFT",
                                    },
                                }
                            ],
                        }
                    ],
                    "pages": {"2": {"size": {"width": 595, "height": 842}}},
                }
            }
        },
    }

    for binding in manifest.providers:
        descriptor = providers.get(binding.id)
        adapter = create_adapter(descriptor)
        if binding.id not in simulated_responses:
            pytest.fail(f"no simulated sidecar payload for math.recognize provider {binding.id}")
        mock_data: dict[str, Any] = simulated_responses[binding.id]

        def handler(request: httpx.Request, data: dict[str, Any] = mock_data) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(200, json={"version": "1.0.0"})
            return httpx.Response(200, json=data)

        transport = httpx.MockTransport(handler)
        base_url = adapter.base_url  # type: ignore[attr-defined]
        adapter._client = lambda timeout=None, t=transport, url=base_url: httpx.Client(  # type: ignore[attr-defined]
            transport=t, base_url=url
        )

        extraction = adapter.execute(
            "math.recognize",
            b"fake-image-bytes",
            filename="formula.png",
            media_type="image/png",
        )

        jsonschema.validate(instance=extraction.document, schema=schema)
        assert isinstance(extraction.document.get("latex"), str)
        assert len(extraction.document["latex"]) > 0
        if binding.id == "docling-math":
            assert extraction.document["page"] == 2
            assert extraction.document["bbox"] == [50.0, 742.0, 500.0, 792.0]

