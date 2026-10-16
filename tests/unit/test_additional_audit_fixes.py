from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_rest_api import CAPABILITY, MANIFEST, PDF

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.docling_document import DoclingServeDocument


@pytest.mark.parametrize(
    "native",
    [
        {"num_rows": "bad", "num_cols": 1, "table_cells": [{"text": "A"}]},
        {
            "num_rows": 1,
            "num_cols": 1,
            "table_cells": [{"text": "A", "start_row_offset_idx": None}],
        },
    ],
)
def test_malformed_native_table_is_structured_provider_error(native, monkeypatch):
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    capabilities = CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)])
    descriptor = ProviderDescriptor.model_validate(
        {"id": "docling", "endpoint": "http://unused", "capabilities": [CAPABILITY]}
    )
    providers = ProviderRegistry([descriptor])

    class Adapter:
        def execute(self, *a, **kw):
            now = datetime.now(UTC)
            return ExtractionResult(
                document=DoclingServeDocument(
                    {
                        "tables": [{"label": "table", "data": native}],
                        "pages": {"1": {"size": {"width": 100, "height": 100}}},
                    }
                ),
                backend="docling",
                started_at=now,
                completed_at=now,
                duration_ms=0,
                version="test",
            )

        def versions(self):
            return {"provider": "test"}

    app = create_app(capabilities, providers)
    app.state.executor = CapabilityExecutor(capabilities, providers, lambda d: Adapter())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/v1/capabilities/{CAPABILITY}:execute", files={"file": ("sample.pdf", b"%PDF", PDF)}
        )
    assert response.status_code == 502
    assert "invalid native table" in str(response.json())
