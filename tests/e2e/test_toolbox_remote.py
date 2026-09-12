"""E2E smoke tests against a remote Acessilia Toolbox.

Requires TOOLBOX_BASE_URL to be set (e.g. https://www.acessilia3.inf.ufg.br/toolbox).
Skipped automatically when the variable is absent — never blocks the local suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

TOOLBOX_BASE_URL = os.getenv("TOOLBOX_BASE_URL", "").rstrip("/")
DATASET_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "dataset" / "input"


def _skip_if_no_url() -> str:
    if not TOOLBOX_BASE_URL:
        pytest.skip("TOOLBOX_BASE_URL not set — skipping E2E tests")
    return TOOLBOX_BASE_URL


def _pick_pdf() -> Path:
    """Pick the smallest PDF from the dataset for quick testing."""
    pdfs = sorted(
        p for p in DATASET_DIR.glob("*.pdf") if p.stat().st_size < 500_000
    )
    if not pdfs:
        pytest.skip("No suitable PDF found in tests/fixtures/dataset/input/")
    return pdfs[0]


# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────


class TestHealth:
    """GET /v1/health"""

    def test_health_returns_200(self) -> None:
        base = _skip_if_no_url()
        resp = httpx.get(f"{base}/v1/health", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_health_returns_version(self) -> None:
        base = _skip_if_no_url()
        resp = httpx.get(f"{base}/v1/health", timeout=10)
        data = resp.json()
        assert "version" in data


class TestCapabilities:
    """GET /v1/capabilities"""

    def test_list_capabilities_returns_list(self) -> None:
        base = _skip_if_no_url()
        resp = httpx.get(f"{base}/v1/capabilities", timeout=10)
        assert resp.status_code == 200
        caps = resp.json()
        assert isinstance(caps, list)
        assert len(caps) > 0

    def test_document_structure_extract_is_present(self) -> None:
        base = _skip_if_no_url()
        resp = httpx.get(f"{base}/v1/capabilities", timeout=10)
        ids = [c["id"] for c in resp.json()]
        assert "document.structure.extract" in ids


class TestProviders:
    """GET /v1/providers"""

    def test_list_providers_returns_list(self) -> None:
        base = _skip_if_no_url()
        resp = httpx.get(f"{base}/v1/providers", timeout=10)
        assert resp.status_code == 200
        providers = resp.json()
        assert isinstance(providers, list)
        assert len(providers) > 0

    def test_docling_provider_is_healthy(self) -> None:
        base = _skip_if_no_url()
        # Health is checked via the dedicated endpoint, not embedded in /v1/providers
        resp = httpx.get(f"{base}/v1/providers/docling/health", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True


class TestExtraction:
    """POST /v1/capabilities/document.structure.extract:execute"""

    def test_extract_returns_succeeded(self) -> None:
        base = _skip_if_no_url()
        pdf_path = _pick_pdf()
        with open(pdf_path, "rb") as f:
            resp = httpx.post(
                f"{base}/v1/capabilities/document.structure.extract:execute",
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={"language": "pt-BR"},
                timeout=300,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "succeeded"

    def test_extract_returns_elements(self) -> None:
        base = _skip_if_no_url()
        pdf_path = _pick_pdf()
        with open(pdf_path, "rb") as f:
            resp = httpx.post(
                f"{base}/v1/capabilities/document.structure.extract:execute",
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={"language": "pt-BR"},
                timeout=300,
            )
        data = resp.json()
        elements = data.get("document", {}).get("elements", [])
        assert len(elements) > 0

    def test_extract_returns_provenance(self) -> None:
        base = _skip_if_no_url()
        pdf_path = _pick_pdf()
        with open(pdf_path, "rb") as f:
            resp = httpx.post(
                f"{base}/v1/capabilities/document.structure.extract:execute",
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={"language": "pt-BR"},
                timeout=300,
            )
        data = resp.json()
        prov = data.get("provenance", {})
        assert "duration_ms" in prov
        assert "provider_version" in prov


class TestArtifacts:
    """POST /v1/artifacts + GET /v1/artifacts/{id}

    Skipped when the artifact store is not configured (e.g. MinIO unreachable).
    """

    def _store_or_skip(self, base: str, pdf_path: Path) -> str:
        """Try to store an artifact; skip tests if storage is not configured."""
        with open(pdf_path, "rb") as f:
            resp = httpx.post(
                f"{base}/v1/artifacts",
                files={"file": (pdf_path.name, f, "application/pdf")},
                timeout=30,
            )
        if resp.status_code == 500:
            detail = resp.json().get("message", "")
            if "no artifact storage provider" in detail:
                pytest.skip("artifact store not configured on this server")
        assert resp.status_code == 200, f"store failed: {resp.text}"
        artifact_id = resp.json().get("artifact_id", "")
        assert artifact_id, "No artifact_id in response"
        return artifact_id

    def test_store_and_retrieve_artifact(self) -> None:
        base = _skip_if_no_url()
        pdf_path = _pick_pdf()
        artifact_id = self._store_or_skip(base, pdf_path)

        retrieve_resp = httpx.get(
            f"{base}/v1/artifacts/{artifact_id}", timeout=30
        )
        assert retrieve_resp.status_code == 200
        assert len(retrieve_resp.content) > 0

    def test_extract_by_artifact_id(self) -> None:
        base = _skip_if_no_url()
        pdf_path = _pick_pdf()
        artifact_id = self._store_or_skip(base, pdf_path)

        resp = httpx.post(
            f"{base}/v1/capabilities/document.structure.extract:execute",
            data={
                "artifact_id": artifact_id,
                "language": "pt-BR",
                "provider": "docling",
            },
            timeout=300,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "succeeded"


class TestPddl:
    """GET /v1/planning/domain + /v1/planning/predicates"""

    def test_domain_endpoint(self) -> None:
        base = _skip_if_no_url()
        resp = httpx.get(f"{base}/v1/planning/domain", timeout=10)
        assert resp.status_code == 200

    def test_predicates_endpoint(self) -> None:
        base = _skip_if_no_url()
        resp = httpx.get(f"{base}/v1/planning/predicates", timeout=10)
        assert resp.status_code == 200
