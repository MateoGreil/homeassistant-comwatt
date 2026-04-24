"""Smoke tests: the integration can be imported and the manifest is valid."""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.comwatt.const import DOMAIN


def test_domain_constant() -> None:
    assert DOMAIN == "comwatt"


def test_manifest_matches_domain() -> None:
    manifest_path = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "comwatt"
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    assert "comwatt-client" in manifest["requirements"][0]
