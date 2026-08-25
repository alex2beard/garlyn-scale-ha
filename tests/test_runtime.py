"""Tests for profile resolution, calculation, and atomic acceptance."""

from datetime import date

import pytest

from custom_components.garlyn_scale.algorithm import ALGORITHM_VERSION, Sex
from custom_components.garlyn_scale.models import UserProfile
from custom_components.garlyn_scale.runtime import (
    AcceptanceStatus,
    InvalidProfileForMeasurementError,
    ScaleRuntime,
    UnknownProfileError,
)
from custom_components.garlyn_scale.transport import Measurement, parse_measurement


def _measurement(measurement_id: str, *, profile_pin: str = "4242") -> Measurement:
    return parse_measurement(
        {
            "protocol_version": 1,
            "scale_id": "scale-1",
            "measurement_id": measurement_id,
            "measured_at": "2026-01-15T12:00:00+00:00",
            "profile_pin": profile_pin,
            "weight_kg": 74.8,
            "bia": {
                "20khz": [410.2, 408.6, 360.4, 355.9, 30.1],
                "100khz": [365.1, 363.8, 315.6, 312.2, 26.5],
            },
        }
    )


def _profile(
    *,
    profile_pin: str = "4242",
    profile_id: str | None = None,
    birth_date: date = date(1991, 6, 15),
) -> UserProfile:
    return UserProfile(
        name="Synthetic profile",
        profile_pin=profile_pin,
        profile_id=profile_id,
        sex=Sex.FEMALE,
        date_of_birth=birth_date,
        height_cm=175,
        athlete_mode=False,
    )


def _runtime(*, max_seen_measurements: int = 256) -> ScaleRuntime:
    profile = _profile()
    return ScaleRuntime(
        "scale-1",
        profiles={profile.profile_pin: profile},
        max_seen_measurements=max_seen_measurements,
    )


def test_retry_is_acknowledged_without_second_acceptance() -> None:
    runtime = _runtime()
    measurement = _measurement("one")
    assert runtime.process(measurement) is AcceptanceStatus.ACCEPTED
    first_processed = runtime.last_processed_measurement
    assert runtime.process(measurement) is AcceptanceStatus.DUPLICATE
    assert runtime.seen_count == 1
    assert runtime.last_measurement is measurement
    assert runtime.last_processed_measurement is first_processed


def test_runtime_pushes_each_accepted_measurement_exactly_once() -> None:
    runtime = _runtime()
    updates: list[object] = []
    remove_listener = runtime.add_listener(updates.append)
    assert runtime.listener_count == 1

    measurement = _measurement("one")
    assert runtime.process(measurement) is AcceptanceStatus.ACCEPTED
    assert updates == []

    runtime.publish_last_processed()
    assert updates == [runtime.last_processed_measurement]
    runtime.publish_last_processed()
    assert runtime.process(measurement) is AcceptanceStatus.DUPLICATE
    runtime.publish_last_processed()
    assert len(updates) == 1

    remove_listener()
    remove_listener()
    assert runtime.listener_count == 0
    runtime.process(_measurement("two"))
    runtime.publish_last_processed()
    assert len(updates) == 1


def test_cache_is_bounded() -> None:
    runtime = _runtime(max_seen_measurements=2)
    first = _measurement("one")
    runtime.process(first)
    runtime.process(_measurement("two"))
    runtime.process(_measurement("three"))
    assert runtime.seen_count == 2
    assert runtime.process(first) is AcceptanceStatus.ACCEPTED


def test_successful_processing_stores_native_compatible_snapshot() -> None:
    runtime = _runtime()
    measurement = _measurement("one")

    assert runtime.process(measurement) is AcceptanceStatus.ACCEPTED

    processed = runtime.last_processed_measurement
    assert processed is not None
    assert processed.measurement is measurement
    assert processed.user_profile.name == "Synthetic profile"
    assert processed.algorithm_profile.age_years == 34
    assert processed.algorithm_version == ALGORITHM_VERSION
    assert processed.result.body_fat_pct == 32.80478286743164
    assert processed.result.body_fat_kg == 24.537979125976562
    assert processed.result.muscle_pct == 62.367652893066406
    assert processed.result.muscle_kg == 46.65100860595703
    assert processed.result.body_water_pct == 49.12552261352539
    assert processed.result.body_water_kg == 36.74589157104492
    assert processed.result.bmr_kcal == 1455
    assert processed.result.bmi == 24.424489974975586
    assert runtime.latest_by_profile["4242"] is processed


def test_latest_snapshot_survives_pin_change_for_same_profile_id() -> None:
    original = _profile(profile_id="a" * 32)
    runtime = ScaleRuntime("scale-1", profiles={"4242": original})
    runtime.process(_measurement("one"))
    original_snapshot = runtime.latest_by_profile["4242"]

    changed = _profile(profile_pin="0012", profile_id=original.profile_id)
    runtime.profiles = {"0012": changed}

    assert runtime.latest_for_profile(changed) is original_snapshot
    runtime.process(_measurement("two", profile_pin="0012"))
    assert set(runtime.latest_by_profile) == {"0012"}
    assert runtime.latest_for_profile(changed) is runtime.latest_by_profile["0012"]


def test_unknown_pin_is_not_committed_or_deduplicated() -> None:
    runtime = ScaleRuntime("scale-1")
    measurement = _measurement("one")

    with pytest.raises(UnknownProfileError) as error:
        runtime.process(measurement)

    assert error.value.profile_pin == "4242"
    assert runtime.seen_count == 0
    assert runtime.last_measurement is None
    assert runtime.last_processed_measurement is None


def test_profile_invalid_at_measurement_time_is_not_committed() -> None:
    profile = _profile(birth_date=date(2026, 1, 16))
    runtime = ScaleRuntime("scale-1", profiles={profile.profile_pin: profile})

    with pytest.raises(InvalidProfileForMeasurementError):
        runtime.process(_measurement("one"))

    assert runtime.seen_count == 0
    assert runtime.last_processed_measurement is None


def test_calculation_failure_leaves_previous_snapshot_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    first = _measurement("one")
    runtime.process(first)
    previous = runtime.last_processed_measurement

    def fail_calculation(data: object) -> object:
        del data
        raise RuntimeError("calculation failed")

    monkeypatch.setattr(
        "custom_components.garlyn_scale.runtime.calculate_body_composition",
        fail_calculation,
    )
    with pytest.raises(RuntimeError, match="calculation failed"):
        runtime.process(_measurement("two"))

    assert runtime.seen_count == 1
    assert runtime.last_measurement is first
    assert runtime.last_processed_measurement is previous
