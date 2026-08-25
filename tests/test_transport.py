"""Tests for the normalized transport contract."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from custom_components.garlyn_scale.transport import (
    TransportValidationError,
    parse_measurement,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "synthetic_reference.json"


@pytest.fixture
def synthetic_payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["measurement"]


def test_parse_synthetic_reference_measurement(
    synthetic_payload: dict[str, object],
) -> None:
    measurement = parse_measurement(
        synthetic_payload, expected_scale_id="synthetic_scale_1"
    )
    assert measurement.profile_pin == "4242"
    assert measurement.weight_kg == pytest.approx(74.8)
    assert measurement.bia_20khz.as_tuple() == pytest.approx(
        (410.2, 408.6, 360.4, 355.9, 30.1)
    )
    assert measurement.bia_100khz.as_tuple() == pytest.approx(
        (365.1, 363.8, 315.6, 312.2, 26.5)
    )
    assert measurement.measured_at.utcoffset() is not None


def test_wrong_protocol_version_is_rejected(
    synthetic_payload: dict[str, object],
) -> None:
    synthetic_payload["protocol_version"] = 2
    with pytest.raises(TransportValidationError, match="unsupported"):
        parse_measurement(synthetic_payload)


def test_scale_id_must_match_webhook(synthetic_payload: dict[str, object]) -> None:
    with pytest.raises(TransportValidationError, match="does not match"):
        parse_measurement(synthetic_payload, expected_scale_id="another-scale")


def test_timestamp_requires_utc_offset(synthetic_payload: dict[str, object]) -> None:
    synthetic_payload["measured_at"] = "2026-01-15T12:00:00"
    with pytest.raises(TransportValidationError, match="UTC offset"):
        parse_measurement(synthetic_payload)


def test_bia_must_have_five_values(synthetic_payload: dict[str, object]) -> None:
    payload = deepcopy(synthetic_payload)
    payload["bia"]["20khz"] = [1, 2, 3, 4]  # type: ignore[index]
    with pytest.raises(TransportValidationError, match="exactly five"):
        parse_measurement(payload)


def test_unknown_fields_are_rejected(synthetic_payload: dict[str, object]) -> None:
    synthetic_payload["unexpected"] = True
    with pytest.raises(TransportValidationError, match="unknown"):
        parse_measurement(synthetic_payload)


def test_non_finite_numbers_are_rejected(synthetic_payload: dict[str, object]) -> None:
    synthetic_payload["weight_kg"] = float("nan")
    with pytest.raises(TransportValidationError, match="finite"):
        parse_measurement(synthetic_payload)
