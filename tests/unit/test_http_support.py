"""Tests for HTTP support and sidecar error extraction utilities."""

from __future__ import annotations

import httpx

from acessilia_toolbox.providers.http_support import extract_error_detail, format_http_error


class TestHttpSupport:
    def test_extract_detail_from_json_dict_detail(self) -> None:
        resp = httpx.Response(500, json={"detail": "No module named 'ftfy'"})
        assert extract_error_detail(resp) == "No module named 'ftfy'"

    def test_extract_detail_from_json_dict_message(self) -> None:
        resp = httpx.Response(400, json={"message": "Malformed expression"})
        assert extract_error_detail(resp) == "Malformed expression"

    def test_extract_detail_from_json_dict_error(self) -> None:
        resp = httpx.Response(500, json={"error": "Internal processing failure"})
        assert extract_error_detail(resp) == "Internal processing failure"

    def test_extract_detail_from_pydantic_validation_list(self) -> None:
        resp = httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "files"],
                        "msg": "field required",
                        "type": "value_error.missing",
                    },
                    {
                        "loc": ["body", "pipeline"],
                        "msg": "invalid choice",
                        "type": "value_error",
                    },
                ]
            },
        )
        detail = extract_error_detail(resp)
        assert "body.files: field required" in detail
        assert "body.pipeline: invalid choice" in detail

    def test_extract_detail_from_plain_text(self) -> None:
        resp = httpx.Response(503, text="Service   Temporarily \n Unavailable ")
        assert extract_error_detail(resp) == "Service Temporarily Unavailable"

    def test_extract_detail_caps_length(self) -> None:
        resp = httpx.Response(500, text="A" * 1000)
        assert len(extract_error_detail(resp, max_length=100)) == 100

    def test_format_http_error_with_detail(self) -> None:
        resp = httpx.Response(500, json={"detail": "CUDA out of memory"})
        msg = format_http_error("pp-formulanet", resp)
        assert msg == "pp-formulanet rejected: HTTP 500 - CUDA out of memory"

    def test_format_http_error_empty_detail(self) -> None:
        resp = httpx.Response(500, text="")
        msg = format_http_error("pp-formulanet", resp)
        assert msg == "pp-formulanet rejected: HTTP 500"
