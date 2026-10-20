"""math-speech adapter behavior, exercised over a simulated sidecar."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.math_speech import MathSpeechProvider
from acessilia_toolbox.providers.mathml_latex import mathml_to_latex

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSION_ANSWER = {
    "version": "1.0.0",
    "mathjax": "3.2.2",
    "mathcat": "0.7.6-rc.4 (95892ab)",
    "node": "22.23.3",
    "capability": "math.convert,math.verbalize",
}
MATHML = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mfrac><mi>a</mi><mi>b</mi></mfrac></math>'


def descriptor() -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": "math-speech",
            "version": "1.0",
            "transport": "http",
            "endpoint": "http://math-speech:5009",
            "capabilities": ["math.convert", "math.verbalize"],
            "timeout_seconds": 5.0,
        }
    )


class Sidecar:
    """Records requests and answers like the math-speech server."""

    def __init__(self, status: int = 200, answer: object | None = None) -> None:
        self.status = status
        self.answer = answer
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=VERSION_ANSWER)
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        if self.answer is not None or self.status != 200:
            return httpx.Response(self.status, json=self.answer)
        if request.url.path == "/convert":
            return httpx.Response(200, json={"latex": body["latex"], "mathml": MATHML})
        spoken = {"mathml": MATHML, "speech": "a sobre b", "language": "pt-BR"}
        return httpx.Response(200, json=spoken)


def provider_with(handler) -> MathSpeechProvider:
    adapter = MathSpeechProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def run(adapter: MathSpeechProvider, capability: str, expression: str, **params: object):
    return adapter.execute(
        capability,
        expression.encode("utf-8"),
        filename="expression.txt",
        media_type="text/plain",
        parameters=params or None,
    )


def schema(name: str) -> dict:
    return json.loads((PROJECT_ROOT / "schemas" / name).read_text(encoding="utf-8"))


class TestConvert:
    def test_latex_to_mathml_goes_through_the_sidecar(self) -> None:
        sidecar = Sidecar()
        result = run(provider_with(sidecar), "math.convert", r"\frac{a}{b}")

        assert sidecar.requests == [("/convert", {"latex": r"\frac{a}{b}"})]
        assert result.document == {
            "latex": r"\frac{a}{b}",
            "mathml": MATHML,
            "direction": "latex-to-mathml",
        }
        assert result.version == "1.0.0"
        jsonschema.validate(result.document, schema("mathml@1.json"))

    def test_json_payload_reads_the_latex_field(self) -> None:
        sidecar = Sidecar()
        provider_with(sidecar).execute(
            "math.convert",
            json.dumps({"latex": "x^2+1", "confidence": 0.9}).encode(),
            filename="expression.json",
            media_type="application/json",
        )
        assert sidecar.requests == [("/convert", {"latex": "x^2+1"})]

    def test_mathml_to_latex_stays_in_process(self) -> None:
        def no_network(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected request to {request.url}")

        result = run(provider_with(no_network), "math.convert", MATHML, direction="mathml-to-latex")

        assert result.document["direction"] == "mathml-to-latex"
        assert result.document["mathml"] == MATHML
        assert r"\frac" in result.document["latex"]

    def test_unknown_direction_is_rejected_before_calling(self) -> None:
        sidecar = Sidecar()
        with pytest.raises(InvalidInputError, match="direction"):
            run(provider_with(sidecar), "math.convert", "x", direction="latex-mathml")
        assert sidecar.requests == []


class TestVerbalize:
    def test_speaks_pt_br_with_default_style(self) -> None:
        sidecar = Sidecar()
        result = run(provider_with(sidecar), "math.verbalize", r"\frac{a}{b}")

        expected_body = {
            "latex": r"\frac{a}{b}",
            "language": "pt-BR",
            "style": "ClearSpeak",
            "verbosity": "Medium",
        }
        assert sidecar.requests == [("/speak", expected_body)]
        assert result.document == {
            "latex": r"\frac{a}{b}",
            "verbalized": "a sobre b",
            "language": "pt-BR",
        }
        assert result.configuration["style"] == "ClearSpeak"
        jsonschema.validate(result.document, schema("verbalized@1.json"))

    def test_style_and_verbosity_are_forwarded(self) -> None:
        sidecar = Sidecar()
        run(provider_with(sidecar), "math.verbalize", "x", style="SimpleSpeak", verbosity="Verbose")
        _, body = sidecar.requests[0]
        assert (body["style"], body["verbosity"]) == ("SimpleSpeak", "Verbose")

    def test_unknown_style_is_rejected_before_calling(self) -> None:
        sidecar = Sidecar()
        with pytest.raises(InvalidInputError, match="style"):
            run(provider_with(sidecar), "math.verbalize", "x", style="Shout")
        assert sidecar.requests == []


class TestErrors:
    """Input the sidecar rejects is the caller's; an outage is a 503."""

    @pytest.mark.parametrize("status", [400, 413])
    def test_rejected_expression_is_invalid_input(self, status: int) -> None:
        sidecar = Sidecar(status, {"detail": "invalid_latex: MissingCloseBrace"})
        with pytest.raises(InvalidInputError, match="invalid_latex: MissingCloseBrace") as excinfo:
            run(provider_with(sidecar), "math.convert", r"\frac{private_source}{")
        assert "private_source" not in str(excinfo.value)
        assert excinfo.value.http_status == 400

    def test_starting_sidecar_is_unavailable(self) -> None:
        sidecar = Sidecar(503, {"detail": "unavailable: MathCAT failed to start"})
        with pytest.raises(ProviderUnavailableError):
            run(provider_with(sidecar), "math.verbalize", "x")

    def test_unreachable_sidecar_is_unavailable(self) -> None:
        def refused(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with pytest.raises(ProviderUnavailableError, match="not reachable"):
            run(provider_with(refused), "math.verbalize", "x")

    def test_timeout(self) -> None:
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(ProviderTimeoutError):
            run(provider_with(slow), "math.convert", "x")

    def test_mathcat_failure_is_an_execution_error(self) -> None:
        sidecar = Sidecar(500, {"detail": "speech_failed: Pattern match/replacement failure!"})
        with pytest.raises(ProviderExecutionError, match="speech_failed"):
            run(provider_with(sidecar), "math.verbalize", r"A \xrightarrow{f} B")

    def test_answer_without_the_field_is_an_execution_error(self) -> None:
        sidecar = Sidecar(200, {"mathml": MATHML})
        with pytest.raises(ProviderExecutionError, match="speech"):
            run(provider_with(sidecar), "math.verbalize", "x")


class TestIdentity:
    def test_versions_keep_only_what_shapes_the_output(self) -> None:
        versions = provider_with(Sidecar()).versions()
        assert versions == {
            "provider": "1.0.0",
            "version": "1.0.0",
            "mathjax": "3.2.2",
            "mathcat": "0.7.6-rc.4 (95892ab)",
        }

    def test_versions_fall_back_to_the_declared_version(self) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        assert provider_with(down).versions() == {"provider": "1.0"}

    def test_health(self) -> None:
        def healthy(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "version": "1.0.0"})

        def starting(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"detail": "unavailable"})

        assert provider_with(healthy).health().healthy is True
        assert provider_with(starting).health().healthy is False

    def test_factory(self) -> None:
        assert isinstance(create_adapter(descriptor()), MathSpeechProvider)

    @pytest.mark.parametrize("capability", ["math.convert", "math.verbalize"])
    def test_default_configuration_resolves_to_math_speech(self, capability: str) -> None:
        """A single provider per capability: no explicit provider needed."""
        registry = ProviderRegistry.from_file(PROJECT_ROOT / "providers-config.yaml")
        assert registry.resolve(capability).id == "math-speech"


class TestMathmlToLatex:
    def test_tokens_and_operators(self) -> None:
        latex = mathml_to_latex("<math><mi>x</mi><mo>+</mo><mn>1</mn></math>")
        assert latex.replace(" ", "") == "x+1"

    def test_square_root(self) -> None:
        assert r"\sqrt{x}" in mathml_to_latex("<math><msqrt><mi>x</mi></msqrt></math>")
