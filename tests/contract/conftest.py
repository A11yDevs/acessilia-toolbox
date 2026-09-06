"""Contract test configuration.

Contract tests run against a live provider. They are skipped when the provider
is unreachable so the fast suite stays runnable without Docker.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import create_adapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_DIR = PROJECT_ROOT / "capabilities"

PROVIDER_ENDPOINTS = {
    "docling": os.getenv("DOCLING_SERVE_URL", "http://localhost:5001"),
    "minio": os.getenv("MINIO_URL", "http://localhost:9000"),
    "valkey": os.getenv("VALKEY_URL", "redis://localhost:6379"),
}


@pytest.fixture(scope="session")
def capabilities() -> CapabilityRegistry:
    return CapabilityRegistry.from_directory(CAPABILITIES_DIR)


@pytest.fixture(scope="session")
def provider_descriptor(request: pytest.FixtureRequest) -> ProviderDescriptor:
    provider_id = getattr(request, "param", "docling")
    manifests = CapabilityRegistry.from_directory(CAPABILITIES_DIR)
    capabilities = [
        manifest.id
        for manifest in manifests.manifests()
        if any(binding.id == provider_id for binding in manifest.providers)
    ]
    return ProviderDescriptor.model_validate(
        {
            "id": provider_id,
            "endpoint": PROVIDER_ENDPOINTS[provider_id],
            "capabilities": capabilities,
        }
    )


@pytest.fixture(scope="session")
def live_provider(provider_descriptor: ProviderDescriptor) -> ProviderDescriptor:
    adapter = create_adapter(provider_descriptor)
    health = adapter.health()
    if not health.healthy:
        pytest.skip(
            f"{provider_descriptor.id} unreachable at {provider_descriptor.endpoint}: "
            f"{health.detail}. Start it with `docker compose up -d`."
        )
    return provider_descriptor


@pytest.fixture
def executor(
    capabilities: CapabilityRegistry, live_provider: ProviderDescriptor
) -> CapabilityExecutor:
    return CapabilityExecutor(
        capabilities, ProviderRegistry([live_provider]), create_adapter
    )


@pytest.fixture(scope="session")
def sample_pdf_bytes() -> bytes:
    """A minimal one-page PDF, built inline to keep the suite self-contained."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 68 >>\nstream\nBT /F1 24 Tf 72 760 Td "
        b"(Test document) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref_at = len(pdf)
    pdf += b"xref\n0 %d\n" % (len(objects) + 1)
    pdf += b"0000000000 65535 f \n"
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(pdf)
