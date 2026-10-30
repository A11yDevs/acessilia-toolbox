"""docling-math adapter behavior, exercised over a simulated transport."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.docling_math import DoclingMathProvider


def top_left_box(left: float, top: float, right: float, bottom: float) -> dict[str, Any]:
    return {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": "TOPLEFT"}


SAMPLE_RESPONSE: dict[str, Any] = {
    "document": {
        "json_content": {
            "schema_name": "DoclingDocument",
            "version": "1.0",
            "name": "test",
            "origin": "test",
            "furniture": {},
            "body": [],
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": {"value": "text"},
                    "text": "E = mc^2",
                    "level": 1,
                    "confidence": 0.98,
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": top_left_box(50, 50, 500, 100),
                        }
                    ],
                },
            ],
            "groups": [
                {
                    "self_ref": "#/groups/0",
                    "label": {"value": "formula"},
                    "text": r"E = mc^2",
                    "confidence": 0.95,
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": top_left_box(50, 50, 500, 100),
                        }
                    ],
                },
                {
                    "self_ref": "#/groups/1",
                    "label": {"value": "formula"},
                    "text": r"\frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
                    "confidence": 0.90,
                    "prov": [
                        {
                            "page_no": 2,
                            "bbox": top_left_box(50, 200, 500, 300),
                        }
                    ],
                },
            ],
            "pictures": [],
            "tables": [],
            "key_value_items": [],
            "form_items": [],
            "pages": {
                "1": {"size": {"width": 595, "height": 842}},
                "2": {"size": {"width": 595, "height": 842}},
            },
        }
    },
    "status": "success",
    "errors": [],
    "processing_time": 1.5,
    "timings": {},
    "confidence": 0.95,
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "docling-math",
        "version": "1.32",
        "endpoint": "http://docling-serve:5001",
        "capabilities": ["math.recognize"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler) -> DoclingMathProvider:
    adapter = DoclingMathProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def convert_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/version":
        return httpx.Response(
            200,
            json={
                "docling-serve": "1.32.0",
                "docling": "2.124.0",
                "docling-core": "2.93.0",
            },
        )
    return httpx.Response(200, json=SAMPLE_RESPONSE)


def handler_for(response: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"docling-serve": "1.32.0"})
        return httpx.Response(200, json=response)

    return handler


def sample_with(**json_content: object) -> dict[str, Any]:
    response = copy.deepcopy(SAMPLE_RESPONSE)
    response["document"]["json_content"].update(json_content)
    return response


def extract(adapter: DoclingMathProvider):
    return adapter.execute(
        "math.recognize",
        b"fake-image-data",
        filename="formula.png",
        media_type="image/png",
    )


class TestDoclingMathProvider:
    def test_extraction_returns_latex_and_conforms_to_schema(self) -> None:
        schema_path = Path(__file__).parents[2] / "schemas" / "latex@1.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        extraction = extract(provider_with(convert_handler))

        assert extraction.backend == "docling"
        assert extraction.version == "1.32.0"
        assert extraction.document["latex"] == "E = mc^2"
        assert extraction.document["confidence"] == 0.95
        assert extraction.document["page"] == 1
        assert extraction.document["bbox"] == [50.0, 50.0, 500.0, 100.0]

        jsonschema.validate(instance=extraction.document, schema=schema)

    def test_multiple_formulas_return_the_first_and_report_the_count(self) -> None:
        """latex@1 is one expression; the others are counted, not concatenated."""
        extraction = extract(provider_with(convert_handler))

        assert "\n" not in extraction.document["latex"]
        assert extraction.configuration["formula_count"] == 2

    def test_page_comes_from_docling_page_no(self) -> None:
        response = sample_with(groups=SAMPLE_RESPONSE["document"]["json_content"]["groups"][1:])

        extraction = extract(provider_with(handler_for(response)))

        assert extraction.document["page"] == 2
        assert extraction.document["confidence"] == 0.90

    def test_bottom_left_bbox_is_flipped_to_top_left(self) -> None:
        response = sample_with(
            groups=[
                {
                    "label": {"value": "formula"},
                    "text": "x",
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": {
                                "l": 285.3,
                                "t": 522.0,
                                "r": 650.0,
                                "b": 471.0,
                                "coord_origin": "BOTTOMLEFT",
                            },
                        }
                    ],
                }
            ]
        )

        extraction = extract(provider_with(handler_for(response)))

        assert extraction.document["bbox"] == [285.3, 320.0, 650.0, 371.0]

    def test_bottom_left_bbox_without_page_height_is_dropped(self) -> None:
        response = sample_with(
            pages={},
            groups=[
                {
                    "label": {"value": "formula"},
                    "text": "x",
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": {"l": 0, "t": 10, "r": 5, "b": 0, "coord_origin": "BOTTOMLEFT"},
                        }
                    ],
                }
            ],
        )

        extraction = extract(provider_with(handler_for(response)))

        assert "bbox" not in extraction.document

    def test_out_of_range_confidence_is_dropped_and_recorded(self) -> None:
        response = sample_with(
            groups=[{"label": {"value": "formula"}, "text": "x", "confidence": "high"}]
        )

        extraction = extract(provider_with(handler_for(response)))

        assert "confidence" not in extraction.document
        assert extraction.configuration["dropped_confidence"] == "high"

    def test_empty_response_raises_when_no_formulas_or_text(self) -> None:
        response = sample_with(groups=[], texts=[])

        with pytest.raises(ProviderExecutionError, match="found no mathematical formulas"):
            extract(provider_with(handler_for(response)))

    def test_plain_text_is_not_returned_as_latex(self) -> None:
        """Prose without a formula label must not reach math.convert as LaTeX."""
        response = sample_with(
            groups=[], texts=[{"label": {"value": "text"}, "text": "a page of prose"}]
        )

        with pytest.raises(ProviderExecutionError, match="found no mathematical formulas"):
            extract(provider_with(handler_for(response)))

    def test_formula_labelled_text_item_is_recognized(self) -> None:
        response = sample_with(
            groups=[], texts=[{"label": {"value": "formula"}, "text": "a^2 + b^2 = c^2"}]
        )

        extraction = extract(provider_with(handler_for(response)))

        assert extraction.document == {"latex": "a^2 + b^2 = c^2", "page": 1}

    def test_timeout_raises_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        with pytest.raises(ProviderTimeoutError):
            extract(provider_with(handler))

    def test_http_error_raises_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422)

        with pytest.raises(ProviderExecutionError):
            extract(provider_with(handler))

    def test_connection_error_raises_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderUnavailableError):
            extract(provider_with(handler))

    def test_health_returns_healthy(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})

        adapter = provider_with(handler)
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "docling-math"

    def test_health_returns_unhealthy_on_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        adapter = provider_with(handler)
        health = adapter.health()
        assert health.healthy is False

    def test_versions_returns_component_info(self) -> None:
        adapter = provider_with(convert_handler)
        versions = adapter.versions()
        assert versions["provider"] == "1.32.0"
        assert versions["docling"] == "2.124.0"

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, DoclingMathProvider)

    def test_configuration_includes_capability(self) -> None:
        extraction = extract(provider_with(convert_handler))
        assert extraction.configuration["capability"] == "math.recognize"
        assert extraction.configuration["extractor"] == "docling-serve"

    def test_label_as_string_not_dict(self) -> None:
        """Handle case where label is a plain string instead of {value: ...}."""
        response = sample_with(
            groups=[
                {
                    "self_ref": "#/groups/0",
                    "label": "formula",
                    "text": r"x = y",
                    "confidence": 0.9,
                    "prov": [{"page": 1, "bbox": [0, 0, 100, 50]}],
                },
            ]
        )

        extraction = extract(provider_with(handler_for(response)))
        assert extraction.document["latex"] == "x = y"
        assert extraction.document["confidence"] == 0.9
        assert extraction.document["page"] == 1
        assert extraction.document["bbox"] == [0.0, 0.0, 100.0, 50.0]
