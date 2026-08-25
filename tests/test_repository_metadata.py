"""Low-cost checks for custom-integration metadata and fixtures."""

import json
import struct
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_manifest_declares_custom_integration_requirements() -> None:
    manifest = json.loads(
        (ROOT / "custom_components/garlyn_scale/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["domain"] == "garlyn_scale"
    assert manifest["config_flow"] is True
    assert manifest["dependencies"] == ["webhook"]
    assert manifest["iot_class"] == "local_push"
    assert manifest["issue_tracker"] == (
        "https://github.com/alex2beard/garlyn-scale-ha/issues"
    )
    assert manifest["version"] == "0.4.0"

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == manifest["version"]


def test_english_translation_matches_source_strings() -> None:
    component = ROOT / "custom_components/garlyn_scale"
    strings = json.loads((component / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (component / "translations/en.json").read_text(encoding="utf-8")
    )
    assert english == strings


def test_hacs_metadata_and_brand_assets_are_present() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs == {"name": "GARLYN Scale"}

    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 alex2beard" in license_text

    brand = ROOT / "custom_components/garlyn_scale/brand"
    expected_dimensions = {"icon.png": (256, 256), "icon@2x.png": (512, 512)}
    for filename, dimensions in expected_dimensions.items():
        data = (brand / filename).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", data[16:24]) == dimensions


def test_public_fixture_is_explicitly_synthetic() -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/synthetic_reference.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["data_origin"] == "synthetic"
    assert "not a device capture" in fixture["description"]
    assert fixture["profile"]["activity_level"] == 0
    assert fixture["profile"]["reference_standard"] == "external"
    assert fixture["measurement"]["scale_id"].startswith("synthetic_")
    assert fixture["measurement"]["measurement_id"].startswith("synthetic-")
