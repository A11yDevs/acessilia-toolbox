"""The committed capability contract matches the manifests and provider config.

``contract/toolbox-contract.json`` is what consumers check their capability and
provider ids against (A11yDevs/acessilia#186), so a change to ``capabilities/``
or ``providers-config.yaml`` must regenerate it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_contract.py"


def _exporter():
    spec = importlib.util.spec_from_file_location("export_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_contract_is_up_to_date() -> None:
    exporter = _exporter()
    committed = exporter.CONTRACT_PATH.read_text(encoding="utf-8")
    assert committed == exporter.render(exporter.build()), (
        "contract/toolbox-contract.json is stale: run python scripts/export_contract.py"
    )


def test_every_capability_lists_only_registered_providers() -> None:
    contract = json.loads(_exporter().CONTRACT_PATH.read_text(encoding="utf-8"))
    registered = {provider["id"] for provider in contract["providers"]}
    for capability in contract["capabilities"]:
        assert set(capability["providers"]) <= registered, capability["id"]
