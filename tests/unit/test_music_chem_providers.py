"""Tests for the specialized-object providers (music, chemistry, tables)."""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.docling import _convert_params
from acessilia_toolbox.providers.docling_chem import DoclingChemProvider
from acessilia_toolbox.providers.music_omr import (
    AudiverisProvider,
    HomrProvider,
    looks_like_music_document,
)
from acessilia_toolbox.providers.pure_chem import PureChemProvider, normalize_mhchem


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "audiveris",
        "version": "0.1",
        "endpoint": "http://audiveris-serve:5003",
        "capabilities": ["music.omr"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


MUSICXML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<score-partwise><part-list/></score-partwise>"
)


def audiveris_handler(
    request: httpx.Request,
) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/version":
        return httpx.Response(200, json={"version": "5.5"})
    if request.url.path == "/convert":
        return httpx.Response(
            200,
            json={
                "notation": MUSICXML,
                "format": "musicxml",
                "confidence": 0.92,
            },
        )
    return httpx.Response(404)


def audiveris_provider(handler=audiveris_handler) -> AudiverisProvider:
    adapter = AudiverisProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


class TestAudiverisProvider:
    def test_convert_returns_score(self) -> None:
        result = audiveris_provider().execute(
            "music.omr",
            b"png-bytes",
            filename="score.png",
            media_type="image/png",
        )
        document = result.document
        assert document["notation"] == MUSICXML
        assert document["format"] == "musicxml"
        assert document["confidence"] == 0.92
        assert result.backend == "audiveris"

    def test_health_ok(self) -> None:
        health = audiveris_provider().health()
        assert health.healthy is True
        assert health.version == "5.5"

    def test_unreachable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        with pytest.raises(ProviderUnavailableError):
            audiveris_provider(handler).execute(
                "music.omr",
                b"png",
                filename="s.png",
                media_type="image/png",
            )

    def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("too slow")

        with pytest.raises(ProviderTimeoutError):
            audiveris_provider(handler).execute(
                "music.omr",
                b"png",
                filename="s.png",
                media_type="image/png",
            )

    def test_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "bad image"})

        with pytest.raises(ProviderExecutionError) as excinfo:
            audiveris_provider(handler).execute(
                "music.omr",
                b"png",
                filename="s.png",
                media_type="image/png",
            )
        assert excinfo.value.details.get("status_code") == 422

    def test_registry_creates_audiveris(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, AudiverisProvider)


class TestHomrProvider:
    """homr is an optional dependency; simulate both environments.

    Tests monkeypatch builtins.__import__ instead of skipping, so they
    are deterministic whether or not the 'music' extra is installed.
    """

    @staticmethod
    def _with_homr(monkeypatch: pytest.MonkeyPatch, available: bool) -> None:
        """Force the homr import attempt to succeed or fail."""
        import builtins

        original_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if (name == "homr" or name.startswith("homr.")) and not available:
                raise ImportError(f"No module named {name!r}")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

    def test_unhealthy_without_homr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._with_homr(monkeypatch, available=False)
        provider = HomrProvider(descriptor(id="homr"))
        health = provider.health()
        assert health.healthy is False
        assert health.detail is not None

    def test_execute_raises_without_homr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._with_homr(monkeypatch, available=False)
        provider = HomrProvider(descriptor(id="homr"))
        with pytest.raises(ProviderUnavailableError):
            provider.execute(
                "music.omr",
                b"png",
                filename="s.png",
                media_type="image/png",
            )

    def test_healthy_with_homr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # homr may or may not be installed; make the import succeed either
        # way with a stand-in module exercising the health probe path.
        import types

        self._with_homr(monkeypatch, available=True)
        stand_in = types.ModuleType("homr")
        monkeypatch.setitem(sys.modules, "homr", stand_in)
        provider = HomrProvider(descriptor(id="homr"))
        health = provider.health()
        assert health.healthy is True

    def test_registry_creates_homr(self) -> None:
        adapter = create_adapter(descriptor(id="homr"))
        assert isinstance(adapter, HomrProvider)

    def test_rejects_other_capability(self) -> None:
        provider = HomrProvider(descriptor(id="homr"))
        with pytest.raises(ValueError):
            provider.execute(
                "document.ocr",
                b"png",
                filename="s.png",
                media_type="image/png",
            )


class TestMusicScoreCheck:
    def test_valid_xml(self) -> None:
        assert looks_like_music_document(MUSICXML, "musicxml") is True

    def test_invalid_xml(self) -> None:
        assert looks_like_music_document("<not-closed", "musicxml") is False

    def test_empty(self) -> None:
        assert looks_like_music_document("", "musicxml") is False


# ---------------------------------------------------------------------------
# pure-chem / mhchem
# ---------------------------------------------------------------------------


class TestNormalizeMhchem:
    def test_single_formula(self) -> None:
        result = normalize_mhchem(r"\ce{H2O}")
        assert result["kind"] == "formula"
        assert result["formula"] == "H2O"
        assert result["text"]

    def test_simple_reaction(self) -> None:
        result = normalize_mhchem(r"\ce{2H2 + O2 -> 2H2O}")
        assert result["kind"] == "reaction"
        assert result["arrow"] == "->"
        assert len(result["reactants"]) == 2
        assert result["products"][0]["coefficient"] == "2"
        assert result["products"][0]["formula"] == "H2O"

    def test_equilibrium_with_conditions(self) -> None:
        result = normalize_mhchem(r"\ce{N2 + 3H2 <=>T[Fe] 2NH3}")
        assert result["arrow"] == "<=>"
        assert result["conditions"] == "Fe"
        assert len(result["reactants"]) == 2

    def test_charge_and_state(self) -> None:
        result = normalize_mhchem(r"\ce{Na+ (aq) + Cl- (aq) -> NaCl (s)}")
        reactants = result["reactants"]
        assert reactants[0]["charge"] == "+"
        assert reactants[0]["state"] == "aq"
        assert result["products"][0]["state"] == "s"

    def test_empty_raises(self) -> None:
        with pytest.raises(ProviderExecutionError):
            normalize_mhchem("")


class TestPureChemProvider:
    def test_execute_formula(self) -> None:
        provider = PureChemProvider(descriptor(id="pure-chem"))
        result = provider.execute(
            "chem.convert",
            br"\ce{CO2}",
            filename="expr.txt",
            media_type="text/plain",
        )
        assert result.document["kind"] == "formula"
        assert result.document["formula"] == "CO2"
        assert result.backend == "pure-python"

    def test_execute_reaction(self) -> None:
        provider = PureChemProvider(descriptor(id="pure-chem"))
        result = provider.execute(
            "chem.convert",
            br"\ce{CH4 + 2O2 -> CO2 + 2H2O}",
            filename="expr.txt",
            media_type="text/plain",
        )
        assert result.document["kind"] == "reaction"
        assert result.document["text"]

    def test_rejects_unknown_capability(self) -> None:
        provider = PureChemProvider(descriptor(id="pure-chem"))
        with pytest.raises(ValueError):
            provider.execute(
                "math.verbalize",
                br"\ce{H2O}",
                filename="x",
                media_type="text/plain",
            )

    def test_registry_creates_pure_chem(self) -> None:
        adapter = create_adapter(descriptor(id="pure-chem"))
        assert isinstance(adapter, PureChemProvider)


# ---------------------------------------------------------------------------
# docling-chem (VLM)
# ---------------------------------------------------------------------------

CHEM_RESPONSE: dict[str, Any] = {
    "document": {
        "md_content": (
            "The reaction shown is $\\ce{2H2 + O2 -> 2H2O}$ as annotated."
        ),
    }
}


def docling_chem_provider(handler) -> DoclingChemProvider:
    adapter = DoclingChemProvider(
        descriptor(
            id="docling-chem",
            endpoint="http://docling-serve:5001",
            capabilities=["chem.recognize"],
        )
    )
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


class TestDoclingChemProvider:
    def test_recognize_extracts_ce(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/convert/file":
                return httpx.Response(200, json=CHEM_RESPONSE)
            return httpx.Response(200, json={"version": "1.32"})

        result = docling_chem_provider(handler).execute(
            "chem.recognize",
            b"png",
            filename="reaction.png",
            media_type="image/png",
        )
        assert result.document["reaction_count"] == 1
        reaction = result.document["reactions"][0]
        assert reaction["kind"] == "reaction"
        assert reaction["arrow"] == "->"
        assert result.document["description"]

    def test_no_reaction(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/convert/file":
                return httpx.Response(
                    200,
                    json={"document": {"md_content": "Just a cat picture."}},
                )
            return httpx.Response(200, json={"version": "1.32"})

        result = docling_chem_provider(handler).execute(
            "chem.recognize",
            b"png",
            filename="cat.png",
            media_type="image/png",
        )
        assert result.document["reaction_count"] == 0

    def test_registry_creates_docling_chem(self) -> None:
        adapter = create_adapter(
            descriptor(
                id="docling-chem",
                endpoint="http://docling-serve:5001",
                capabilities=["chem.recognize"],
            )
        )
        assert isinstance(adapter, DoclingChemProvider)


# ---------------------------------------------------------------------------
# docling table parameters
# ---------------------------------------------------------------------------


class TestConvertParams:
    def test_config_default_applied(self) -> None:
        desc = descriptor(
            id="docling",
            capabilities=["document.structure.extract"],
            config={"table_mode": "accurate"},
        )
        params = _convert_params(desc, None)
        assert params == {"table_mode": "accurate"}

    def test_request_overrides_config(self) -> None:
        desc = descriptor(
            id="docling",
            capabilities=["document.structure.extract"],
            config={"table_mode": "accurate"},
        )
        params = _convert_params(desc, {"table_mode": "fast"})
        assert params == {"table_mode": "fast"}

    def test_unknown_keys_ignored(self) -> None:
        desc = descriptor(
            id="docling",
            capabilities=["document.structure.extract"],
        )
        params = _convert_params(desc, {"evil_param": "x", "table_mode": "fast"})
        assert params == {"table_mode": "fast"}

    def test_bool_serialized(self) -> None:
        desc = descriptor(
            id="docling",
            capabilities=["document.structure.extract"],
        )
        params = _convert_params(desc, {"do_table_structure": True})
        assert params == {"do_table_structure": "true"}

    def test_vlm_pipeline_forwarded(self) -> None:
        desc = descriptor(
            id="docling",
            capabilities=["document.structure.extract"],
        )
        params = _convert_params(
            desc, {"pipeline": "vlm", "vlm_engine": "granitedocling"}
        )
        assert params == {"pipeline": "vlm", "vlm_engine": "granitedocling"}


# ---------------------------------------------------------------------------
# capability manifests / config sanity
# ---------------------------------------------------------------------------


class TestCapabilityRegistration:
    def test_music_omr_capability_exists(self) -> None:
        from acessilia_toolbox.core.capability import CapabilityRegistry

        registry = CapabilityRegistry.from_directory(
            __import__("pathlib").Path("capabilities")
        )
        capability = registry.get("music.omr")
        assert capability is not None
        provider_ids = [provider.id for provider in capability.providers]
        assert "homr" in provider_ids
        assert "audiveris" in provider_ids

    def test_chem_capabilities_exist(self) -> None:
        from acessilia_toolbox.core.capability import CapabilityRegistry

        registry = CapabilityRegistry.from_directory(
            __import__("pathlib").Path("capabilities")
        )
        for capability_id in ("chem.recognize", "chem.convert"):
            capability = registry.get(capability_id)
            assert capability is not None, capability_id

    def test_schemas_are_valid_json(self) -> None:
        from pathlib import Path

        for name in ("music-score@1.json", "mhchem@1.json"):
            payload = json.loads(
                (Path("schemas") / name).read_text(encoding="utf-8")
            )
            assert payload["$id"]
