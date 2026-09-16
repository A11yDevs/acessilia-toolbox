"""Nougat adapter behavior, exercised over simulated transport."""

from __future__ import annotations

import httpx
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.nougat import NougatProvider


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "nougat",
        "version": "0.1.17",
        "endpoint": "http://nougat-serve:5004",
        "capabilities": ["document.structure.extract"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler) -> NougatProvider:
    adapter = NougatProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def test_registered_in_factory() -> None:
    adapter = create_adapter(descriptor())
    assert isinstance(adapter, NougatProvider)


def test_health_reports_healthy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.1.17"})
        return httpx.Response(404)

    health = provider_with(handler).health()
    assert health.healthy is True
    assert health.provider == "nougat"
    assert health.version == "0.1.17"


def test_health_reports_unhealthy_on_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service starting")

    health = provider_with(handler).health()
    assert health.healthy is False
    assert "503" in (health.detail or "")


def test_execute_converts_markdown_to_extraction_result() -> None:
    nougat_markdown = (
        "# Introduction to Quantum\n\n"
        "This paper introduces basic concepts.\n\n"
        r"\[ E = mc^2 \]"
        "\n\n"
        r"\begin{table} data \end{table}"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/predict":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"text": nougat_markdown, "pages": [{"text": nougat_markdown, "page_number": 1}]},
            )
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.1.17"})
        return httpx.Response(404)

    provider = provider_with(handler)
    result = provider.execute(
        "document.structure.extract",
        b"%PDF-1.4 dummy",
        filename="paper.pdf",
        media_type="application/pdf",
    )

    assert result.backend == "nougat"
    assert result.version == "0.1.17"
    assert result.configuration["extractor"] == "nougat"

    items = list(result.document.iterate_items())
    assert len(items) == 4

    labels = [item[0].label.value for item in items]
    assert labels == ["heading", "paragraph", "formula", "table"]


def test_execute_handles_timeout() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(ProviderTimeoutError):
        provider_with(handler).execute(
            "document.structure.extract",
            b"%PDF-1.4",
            filename="paper.pdf",
            media_type="application/pdf",
        )


def test_execute_handles_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderUnavailableError):
        provider_with(handler).execute(
            "document.structure.extract",
            b"%PDF-1.4",
            filename="paper.pdf",
            media_type="application/pdf",
        )


def test_execute_handles_server_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    with pytest.raises(ProviderExecutionError):
        provider_with(handler).execute(
            "document.structure.extract",
            b"%PDF-1.4",
            filename="paper.pdf",
            media_type="application/pdf",
        )
