"""Both deployment paths can select the same external artifact filesystem."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.staging.yml"])
def test_shared_fallback_uses_an_external_nfs_export(filename: str) -> None:
    config = yaml.safe_load((ROOT / filename).read_text())
    mount = config["services"]["toolbox"]["volumes"]
    expected_mount = (
        "${ARTIFACT_FALLBACK_SOURCE:-artifact-fallback}:/tmp/acessilia-toolbox-artifacts"
    )
    assert expected_mount in mount
    shared = config["volumes"]["artifact-shared"]
    assert shared["driver"] == "local"
    assert shared["driver_opts"] == {
        "type": "nfs",
        "o": "addr=${ARTIFACT_NFS_SERVER:-},nfsvers=4,rw,hard",
        "device": ":${ARTIFACT_NFS_EXPORT:-}",
    }
    assert "artifact-fallback" in config["volumes"]
