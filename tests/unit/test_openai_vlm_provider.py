"""OpenAI-compatible vision-language model adapter.

The server is simulated with httpx.MockTransport; each test asserts the request
the adapter sends (prompt, image, sampling, logprobs) and the document it
returns, validated against the published output schema.
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import (
    ConfigurationError,
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import create_adapter, openai_vlm
from acessilia_toolbox.providers.openai_vlm import (
    JUDGE_PROMPTS,
    PAGE_PROMPT,
    OpenAiCompatibleVlmProvider,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = PROJECT_ROOT / "schemas"
PNG = b"\x89PNG\r\n\x1a\nfake-page"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _fresh_limiters():
    openai_vlm._SEMAPHORES.clear()
    yield
    openai_vlm._SEMAPHORES.clear()


def descriptor(**config: Any) -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": "qwen3-vl-8b",
            "version": "test-weights",
            "endpoint": "http://vlm:8080",
            "capabilities": list(openai_vlm.CAPABILITIES),
            "timeout_seconds": 5.0,
            "config": {"model": "qwen3-vl-8b", **config},
        }
    )


def provider_with(handler: Handler, **config: Any) -> OpenAiCompatibleVlmProvider:
    adapter = OpenAiCompatibleVlmProvider(descriptor(**config))
    transport = httpx.MockTransport(handler)
    build = adapter._client

    def client(timeout: float | None = None) -> httpx.Client:
        real = build(timeout)
        return httpx.Client(
            transport=transport, base_url=adapter.base_url, headers=real.headers
        )

    adapter._client = client  # type: ignore[method-assign]
    return adapter


def chat_response(content: str = "", *, finish: str = "stop", logprobs=None) -> httpx.Response:
    choice: dict[str, Any] = {
        "index": 0,
        "message": {"role": "assistant", "content": content},
        "finish_reason": finish,
    }
    if logprobs is not None:
        choice["logprobs"] = {
            "content": [
                {
                    "token": logprobs[0][0],
                    "logprob": logprobs[0][1],
                    "top_logprobs": [{"token": t, "logprob": lp} for t, lp in logprobs],
                }
            ]
        }
    return httpx.Response(200, json={"model": "qwen3-vl-8b", "choices": [choice]})


def schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / f"{name}@1.json").read_text(encoding="utf-8"))


def body_of(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def text_part(body: dict[str, Any]) -> str:
    return next(p["text"] for p in body["messages"][0]["content"] if p["type"] == "text")


def image_part(body: dict[str, Any]) -> str:
    return next(
        p["image_url"]["url"] for p in body["messages"][0]["content"] if p["type"] == "image_url"
    )


def run(adapter, capability: str, parameters=None):
    return adapter.execute(
        capability, PNG, filename="page.png", media_type="image/png", parameters=parameters
    )


# -- document.ocr.page --------------------------------------------------------


def test_page_transcription_sends_the_validated_prompt_and_image() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        seen.append(body_of(request))
        return chat_response("```markdown\n# Title\n\nBody text.\n```")

    adapter = provider_with(
        handler, page_max_tokens=1234, page_sampling={"repeat_penalty": 1.05}
    )
    result = run(adapter, "document.ocr.page")

    [body] = seen
    assert text_part(body) == PAGE_PROMPT
    assert image_part(body) == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert body["temperature"] == 0
    assert body["max_tokens"] == 1234
    assert body["repeat_penalty"] == 1.05
    assert "logprobs" not in body
    assert result.document == {
        "markdown": "# Title\n\nBody text.",
        "finish": "stop",
        "model": "qwen3-vl-8b",
    }
    jsonschema.validate(result.document, schema("page-transcription"))


def test_page_transcription_reports_a_truncated_generation() -> None:
    adapter = provider_with(lambda r: chat_response("partial", finish="length"))
    assert run(adapter, "document.ocr.page").document["finish"] == "length"


# -- document.adjudicate.page -------------------------------------------------


def test_judge_asks_both_orders_and_debiases() -> None:
    seen: list[dict[str, Any]] = []
    # p("2") = 0.8 in the first order, 0.4 in the second.
    answers = iter(
        [
            [("2", math.log(0.8)), ("1", math.log(0.2))],
            [("2", math.log(0.4)), ("1", math.log(0.6))],
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(body_of(request))
        return chat_response("2", logprobs=next(answers))

    adapter = provider_with(handler)
    result = run(
        adapter,
        "document.adjudicate.page",
        {"candidate_a": "texto A", "candidate_b": "texto B", "prompt": "err"},
    )

    first, second = (text_part(b) for b in seen)
    assert first == JUDGE_PROMPTS["err"].format(c1="texto A", c2="texto B")
    assert second == JUDGE_PROMPTS["err"].format(c1="texto B", c2="texto A")
    for body in seen:
        assert body["max_tokens"] == 1
        assert body["logprobs"] is True and body["top_logprobs"] == 20
        assert body["temperature"] == 0
    document = result.document
    assert document["pB_ab"] == pytest.approx(0.8)
    assert document["pA_ba"] == pytest.approx(0.4)
    assert document["pB"] == pytest.approx((0.8 + 1 - 0.4) / 2)
    assert document["prompt"] == "err"
    jsonschema.validate(document, schema("page-judgement"))


def test_judge_cuts_each_candidate_to_the_configured_length() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(text_part(body_of(request)))
        return chat_response("1", logprobs=[("1", 0.0)])

    adapter = provider_with(handler, judge_max_chars=5)
    run(adapter, "document.adjudicate.page", {"candidate_a": "A" * 50, "candidate_b": "B" * 50})

    assert seen[0] == JUDGE_PROMPTS["base"].format(c1="AAAAA", c2="BBBBB")


def test_judge_without_logprobs_fails_instead_of_guessing() -> None:
    adapter = provider_with(lambda r: chat_response("2"))
    with pytest.raises(ProviderExecutionError, match="logprobs"):
        run(adapter, "document.adjudicate.page", {"candidate_a": "a", "candidate_b": "b"})


@pytest.mark.parametrize(
    "parameters",
    [
        {"candidate_a": "a"},
        {"candidate_a": "a", "candidate_b": 3},
        {"candidate_a": "a", "candidate_b": "b", "prompt": "nope"},
    ],
)
def test_judge_rejects_bad_parameters(parameters) -> None:
    adapter = provider_with(lambda r: pytest.fail("no request expected"))
    with pytest.raises(InvalidInputError):
        run(adapter, "document.adjudicate.page", parameters)


# -- document.adjudicate.region -----------------------------------------------


def test_region_lists_the_hypotheses_and_cleans_the_answer() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(body_of(request))
        return chat_response("```\nTexto exato.\n```")

    adapter = provider_with(handler, region_max_chars=4)
    result = run(
        adapter, "document.adjudicate.region", {"candidates": ["docling text", "", "mineru"]}
    )

    prompt = text_part(seen[0])
    assert 'Hypothesis A:\n"""docl"""' in prompt
    assert 'Hypothesis B:\n"""mine"""' in prompt
    assert "Hypothesis C" not in prompt
    assert seen[0]["max_tokens"] == 1024
    assert result.document == {"text": "Texto exato.", "model": "qwen3-vl-8b"}
    jsonschema.validate(result.document, schema("region-transcription"))


def test_region_without_readable_text_returns_empty() -> None:
    adapter = provider_with(lambda r: chat_response("<EMPTY>"))
    result = run(adapter, "document.adjudicate.region", {"candidates": ["x"]})
    assert result.document["text"] == ""


def test_region_rejects_non_list_candidates() -> None:
    adapter = provider_with(lambda r: pytest.fail("no request expected"))
    with pytest.raises(InvalidInputError):
        run(adapter, "document.adjudicate.region", {"candidates": "x"})


# -- transport, auth and errors -----------------------------------------------


def test_api_key_is_sent_as_bearer_token() -> None:
    headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization"))
        return chat_response("x")

    run(provider_with(handler, api_key="secret"), "document.ocr.page")
    run(provider_with(handler, api_key=""), "document.ocr.page")
    # An unset ${VLM_API_KEY} placeholder must not leak as a token.
    run(provider_with(handler, api_key="${VLM_API_KEY}"), "document.ocr.page")

    assert headers == ["Bearer secret", None, None]


def test_context_overflow_is_reported_as_invalid_input() -> None:
    adapter = provider_with(
        lambda r: httpx.Response(
            400, json={"error": {"message": "the request exceeds the available context size"}}
        )
    )
    with pytest.raises(InvalidInputError, match="context window"):
        run(adapter, "document.ocr.page")


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (httpx.ReadTimeout("slow"), ProviderTimeoutError),
        (httpx.ConnectError("refused"), ProviderUnavailableError),
    ],
)
def test_transport_failures_map_to_provider_errors(side_effect, error) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise side_effect

    with pytest.raises(error):
        run(provider_with(handler), "document.ocr.page")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
    ],
)
def test_bad_server_responses_fail(response) -> None:
    with pytest.raises(ProviderExecutionError):
        run(provider_with(lambda r: response), "document.ocr.page")


def test_unknown_capability_is_refused() -> None:
    with pytest.raises(ProviderExecutionError, match="does not implement"):
        run(provider_with(lambda r: chat_response("x")), "document.ocr")


def test_model_is_required() -> None:
    bad = descriptor()
    bad.config.pop("model")
    with pytest.raises(ConfigurationError, match=r"config\.model"):
        OpenAiCompatibleVlmProvider(bad)


def test_concurrency_is_limited_per_provider() -> None:
    first = OpenAiCompatibleVlmProvider(descriptor(max_concurrency=2))
    second = OpenAiCompatibleVlmProvider(descriptor(max_concurrency=2))
    assert first._slots is second._slots
    assert first._slots.acquire(blocking=False) and first._slots.acquire(blocking=False)
    assert not second._slots.acquire(blocking=False)


def test_versions_name_the_declared_weights() -> None:
    adapter = OpenAiCompatibleVlmProvider(descriptor())
    assert adapter.versions() == {"provider": "test-weights", "model": "qwen3-vl-8b"}


@pytest.mark.parametrize(("status", "healthy"), [(200, True), (503, False)])
def test_health(status: int, healthy: bool) -> None:
    adapter = provider_with(lambda r: httpx.Response(status, json={"status": "ok"}))
    assert adapter.health().healthy is healthy


# -- through the executor, with the shipped manifests --------------------------


def test_executor_serves_the_judge_with_the_shipped_manifest() -> None:
    capabilities = CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities")
    providers = ProviderRegistry([descriptor()])
    adapter = provider_with(lambda r: chat_response("2", logprobs=[("2", 0.0)]))
    executor = CapabilityExecutor(capabilities, providers, lambda _: adapter)

    result = executor.execute(
        "document.adjudicate.page",
        PNG,
        filename="page.png",
        media_type="image/png",
        parameters={"candidate_a": "a", "candidate_b": "b"},
    )

    assert result.provider == "qwen3-vl-8b"
    assert result.document["pB"] == pytest.approx(0.5)
    jsonschema.validate(result.document, schema("page-judgement"))


def test_executor_refuses_media_the_server_cannot_decode() -> None:
    capabilities = CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities")
    providers = ProviderRegistry([descriptor()])
    executor = CapabilityExecutor(
        capabilities, providers, lambda _: provider_with(lambda r: pytest.fail("no call"))
    )
    from acessilia_toolbox.core.errors import UnsupportedMediaTypeError

    with pytest.raises(UnsupportedMediaTypeError):
        executor.execute(
            "document.ocr.page", PNG, filename="page.tiff", media_type="image/tiff"
        )


def test_shipped_configuration_builds_the_adapter(monkeypatch) -> None:
    monkeypatch.setenv("VLM_SERVE_URL", "http://vlm:8080")
    registry = ProviderRegistry.from_file(PROJECT_ROOT / "providers-config.yaml")
    adapter = create_adapter(registry.get("qwen3-vl-8b"))
    assert isinstance(adapter, OpenAiCompatibleVlmProvider)
    assert adapter.base_url == "http://vlm:8080"
    # The key is configuration, so it is masked wherever providers are listed.
    assert registry.get("qwen3-vl-8b").public_payload()["config"]["api_key"] == "***"


# -- table.recognize -----------------------------------------------------------

# vlm_tables.py's TABLE_PROMPT, verbatim, so a drift from the experiment shows up.
ORIGINAL_TABLE_PROMPT = """You are a precise table recognition system.
The image is a table cropped from a document page. Two OCR systems produced these HTML hypotheses (may contain
errors in structure or text):

{cands}

Reproduce the table in the image as HTML.
- Output a single <table> using only <tr>, <td> (use <th> only for header cells), with colspan/rowspan where cells span.
- One <tr> per visual row; every visual cell becomes one cell, empty cells as <td></td>.
- Transcribe cell text EXACTLY as printed (numbers, punctuation, symbols, capitalisation). No styles, no markdown.
Return ONLY the HTML table."""


def test_table_default_reproduces_the_experiment() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(body_of(request))
        return chat_response("```html\n<table><tr><td>1</td></tr></table>\n```")

    adapter = provider_with(handler, table_hint_chars=4)
    result = run(adapter, "table.recognize", {"hints": ["<table>docling", "<table>mineru"]})

    expected = ORIGINAL_TABLE_PROMPT.format(
        cands="Hypothesis A:\n<tab\n\nHypothesis B:\n<tab"
    )
    assert all(text_part(body) == expected for body in seen)
    greedy, *samples = seen
    assert greedy["temperature"] == 0 and "seed" not in greedy
    assert [s["seed"] for s in samples] == [0, 1, 2]
    assert all(s["temperature"] == 0.7 and s["top_p"] == 0.95 for s in samples)
    assert all(body["max_tokens"] == 6000 for body in seen)

    document = result.document
    assert document["greedy"] == {"text": "<table><tr><td>1</td></tr></table>", "finish": "stop"}
    assert [s["seed"] for s in document["samples"]] == [0, 1, 2]
    assert document["use_hints"] is True and document["header_cells"] == "th"
    jsonschema.validate(document, schema("table-candidates"))


def test_table_variants_for_testing_the_experiment() -> None:
    """Without hints and with <td> headers, to test anchoring and tag choices."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(text_part(body_of(request)))
        return chat_response("<table></table>", finish="length")

    adapter = provider_with(handler)
    result = run(
        adapter,
        "table.recognize",
        {"hints": ["<table>x</table>"], "use_hints": False, "header_cells": "td",
         "samples": 1, "temperature": 1.0, "seed": 7},
    )

    assert len(seen) == 2
    assert "hypotheses" not in seen[0] and "<table>x</table>" not in seen[0]
    assert "<tr> and <td> (header cells too)" in seen[0]
    assert "<th>" not in seen[0]
    assert result.document["samples"] == [{"text": "<table></table>", "finish": "length", "seed": 7}]
    jsonschema.validate(result.document, schema("table-candidates"))


def test_table_greedy_only_makes_one_request() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return chat_response("<table></table>")

    run(provider_with(handler), "table.recognize", {"samples": 0})
    assert calls == [1]


@pytest.mark.parametrize(
    "parameters",
    [
        {"hints": "x"},
        {"header_cells": "thead"},
        {"samples": 99},
        {"samples": -1},
        {"temperature": "hot"},
    ],
)
def test_table_rejects_bad_parameters(parameters) -> None:
    adapter = provider_with(lambda r: pytest.fail("no request expected"))
    with pytest.raises(InvalidInputError):
        run(adapter, "table.recognize", parameters)
