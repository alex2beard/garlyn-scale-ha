"""Tests for restart-safe runtime serialization and restoration."""

from datetime import date

import pytest

from custom_components.garlyn_scale.algorithm import Sex
from custom_components.garlyn_scale.models import UserProfile
from custom_components.garlyn_scale.runtime import AcceptanceStatus, ScaleRuntime
from custom_components.garlyn_scale.storage import (
    restore_runtime_state,
    serialize_runtime_state,
)
from custom_components.garlyn_scale.transport import Measurement, parse_measurement


def _profile(
    *,
    profile_pin: str = "4242",
    profile_id: str | None = None,
) -> UserProfile:
    return UserProfile(
        name="Synthetic profile",
        profile_pin=profile_pin,
        profile_id=profile_id,
        sex=Sex.FEMALE,
        date_of_birth=date(1991, 6, 15),
        height_cm=175,
        athlete_mode=False,
    )


def _measurement(measurement_id: str) -> Measurement:
    return parse_measurement(
        {
            "protocol_version": 1,
            "scale_id": "scale-1",
            "measurement_id": measurement_id,
            "measured_at": "2026-01-15T12:00:00+00:00",
            "profile_pin": "4242",
            "weight_kg": 74.8,
            "bia": {
                "20khz": [410.2, 408.6, 360.4, 355.9, 30.1],
                "100khz": [365.1, 363.8, 315.6, 312.2, 26.5],
            },
        }
    )


def _runtime(*, max_seen_measurements: int = 256) -> ScaleRuntime:
    profile = _profile()
    return ScaleRuntime(
        "scale-1",
        profiles={profile.profile_pin: profile},
        max_seen_measurements=max_seen_measurements,
    )


def test_runtime_state_round_trip_survives_restart() -> None:
    original = _runtime()
    measurement = _measurement("measurement-1")
    assert original.process(measurement) is AcceptanceStatus.ACCEPTED
    stored = serialize_runtime_state(original)

    restored = _runtime()
    restore_runtime_state(restored, stored)

    assert restored.seen_measurement_ids == ("measurement-1",)
    assert restored.last_measurement == measurement
    assert restored.last_processed_measurement is not None
    assert restored.last_processed_measurement.result.bmr_kcal == 1455
    assert restored.latest_by_profile["4242"].user_profile == _profile()
    assert restored.process(measurement) is AcceptanceStatus.DUPLICATE
    assert serialize_runtime_state(restored) == stored


def test_legacy_runtime_snapshot_without_profile_id_still_restores() -> None:
    original = _runtime()
    original.process(_measurement("measurement-1"))
    stored = serialize_runtime_state(original)
    latest = stored["latest_by_profile"]
    assert isinstance(latest, dict)
    processed = latest["4242"]
    assert isinstance(processed, dict)
    profile = processed["profile"]
    assert isinstance(profile, dict)
    del profile["profile_id"]

    restored = _runtime()
    restore_runtime_state(restored, stored)

    assert restored.last_processed_measurement is not None
    assert (
        restored.last_processed_measurement.user_profile.profile_id
        == _profile().profile_id
    )


def test_restored_snapshot_is_found_after_configured_pin_change() -> None:
    original_profile = _profile(profile_id="a" * 32)
    original = ScaleRuntime("scale-1", profiles={"4242": original_profile})
    original.process(_measurement("measurement-1"))

    changed_profile = _profile(
        profile_pin="0012",
        profile_id=original_profile.profile_id,
    )
    restored = ScaleRuntime("scale-1", profiles={"0012": changed_profile})
    restore_runtime_state(restored, serialize_runtime_state(original))

    assert restored.latest_for_profile(changed_profile) is not None
    assert restored.latest_for_profile(changed_profile).measurement.weight_kg == 74.8


def test_restore_keeps_only_newest_ids_when_cache_limit_decreases() -> None:
    original = _runtime(max_seen_measurements=3)
    for measurement_id in ("one", "two", "three"):
        original.process(_measurement(measurement_id))

    restored = _runtime(max_seen_measurements=2)
    restore_runtime_state(restored, serialize_runtime_state(original))

    assert restored.seen_measurement_ids == ("two", "three")
    assert restored.process(_measurement("three")) is AcceptanceStatus.DUPLICATE
    assert restored.process(_measurement("one")) is AcceptanceStatus.ACCEPTED


def test_invalid_state_is_rejected_before_runtime_is_changed() -> None:
    runtime = _runtime()
    invalid = {
        "scale_id": "another-scale",
        "seen_measurement_ids": [],
        "latest_by_profile": {},
        "last_profile_pin": None,
    }

    with pytest.raises(ValueError, match="different scale"):
        restore_runtime_state(runtime, invalid)

    assert runtime.seen_count == 0
    assert runtime.last_processed_measurement is None


def test_duplicate_stored_ids_are_rejected_without_partial_restore() -> None:
    original = _runtime()
    original.process(_measurement("measurement-1"))
    stored = serialize_runtime_state(original)
    stored["seen_measurement_ids"] = ["measurement-1", "measurement-1"]
    restored = _runtime()

    with pytest.raises(ValueError, match="must be unique"):
        restore_runtime_state(restored, stored)

    assert restored.seen_count == 0
    assert restored.last_processed_measurement is None
