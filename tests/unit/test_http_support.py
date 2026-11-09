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

    def test_extract_detail_from_validation_list_without_loc(self) -> None:
        resp = httpx.Response(422, json={"detail": [{"msg": "field required"}]})
        assert extract_error_detail(resp) == "field required"

    def test_extract_detail_from_plain_text(self) -> None:
        resp = httpx.Response(503, text="Service   Temporarily \n Unavailable ")
        assert extract_error_detail(resp) == "Service Temporarily Unavailable"

    def test_extract_detail_caps_length(self) -> None:
        resp = httpx.Response(500, text="A" * 1000)
        assert len(extract_error_detail(resp, max_length=100)) == 100

    def test_extract_detail_caps_json_detail_length(self) -> None:
        resp = httpx.Response(500, json={"detail": "B" * 1000})
        assert len(extract_error_detail(resp, max_length=100)) == 100

    def test_extract_detail_strips_control_characters(self) -> None:
        resp = httpx.Response(500, text="crash\x00\x01\x07 while importing")
        assert extract_error_detail(resp) == "crash while importing"

    def test_extract_detail_strips_ansi_escapes(self) -> None:
        resp = httpx.Response(500, text="\x1b[31mfatal\x1b[0m: no CUDA device")
        assert extract_error_detail(resp) == "fatal: no CUDA device"

    def test_extract_detail_keeps_non_ascii_text(self) -> None:
        resp = httpx.Response(500, json={"detail": "Falha na conversão"})
        assert extract_error_detail(resp) == "Falha na conversão"

    def test_extract_detail_from_body_without_known_key(self) -> None:
        resp = httpx.Response(500, json={"code": 500, "errors": ["boom"]})
        assert extract_error_detail(resp) == '{"code":500,"errors":["boom"]}'

    def test_extract_detail_null_detail_is_empty(self) -> None:
        resp = httpx.Response(500, json={"detail": None})
        assert extract_error_detail(resp) == ""

    def test_extract_detail_empty_json_body(self) -> None:
        resp = httpx.Response(500, content=b"")
        assert extract_error_detail(resp) == ""

    def test_extract_detail_invalid_json_with_json_content_type(self) -> None:
        resp = httpx.Response(
            502,
            text="upstream crashed",
            headers={"content-type": "application/json"},
        )
        assert extract_error_detail(resp) == "upstream crashed"

    def test_extract_detail_bounded_binary_body(self) -> None:
        resp = httpx.Response(500, content=b"\x00\x01\x02\xff\xfe")
        assert extract_error_detail(resp, max_length=10) == ""

    def test_format_http_error_with_detail(self) -> None:
        resp = httpx.Response(500, json={"detail": "CUDA out of memory"})
        msg = format_http_error("pp-formulanet", resp)
        assert msg == "pp-formulanet rejected: HTTP 500 - CUDA out of memory"

    def test_format_http_error_with_action(self) -> None:
        resp = httpx.Response(422, json={"detail": "bad file"})
        msg = format_http_error("mineru-api", resp, action="rejected the document")
        assert msg == "mineru-api rejected the document: HTTP 422 - bad file"

    def test_extract_detail_from_nested_error_dict(self) -> None:
        resp = httpx.Response(500, json={"error": {"message": "Resource exhausted"}})
        assert extract_error_detail(resp) == "Resource exhausted"

    def test_extract_detail_from_json_list(self) -> None:
        resp = httpx.Response(400, json=["Invalid token", "Expired session"])
        assert extract_error_detail(resp) == "Invalid token Expired session"

    def test_extract_detail_from_json_string(self) -> None:
        resp = httpx.Response(500, json="Direct string error")
        assert extract_error_detail(resp) == "Direct string error"

    def test_format_http_error_empty_detail(self) -> None:
        resp = httpx.Response(500, text="")
        msg = format_http_error("pp-formulanet", resp)
        assert msg == "pp-formulanet rejected: HTTP 500"
