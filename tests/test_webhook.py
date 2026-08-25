"""Tests for the thin aiohttp webhook adapter."""

import asyncio
import json
from datetime import date
from typing import Any

import pytest

from custom_components.garlyn_scale.algorithm import Sex
from custom_components.garlyn_scale.models import UserProfile
from custom_components.garlyn_scale.runtime import ScaleRuntime
from custom_components.garlyn_scale.webhook import async_handle_measurement


class FakeRequest:
    """Minimal request surface used by the webhook handler."""

    def __init__(
        self,
        payload: object | None = None,
        *,
        content_length: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._error = error
        self.content_length = content_length

    async def json(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._payload


def _payload() -> dict[str, object]:
    return {
        "protocol_version": 1,
        "scale_id": "scale-1",
        "measurement_id": "measurement-1",
        "measured_at": "2026-01-15T12:00:00+00:00",
        "profile_pin": "4242",
        "weight_kg": 74.8,
        "bia": {
            "20khz": [410.2, 408.6, 360.4, 355.9, 30.1],
            "100khz": [365.1, 363.8, 315.6, 312.2, 26.5],
        },
    }


def _profile(*, birth_date: date = date(1991, 6, 15)) -> UserProfile:
    return UserProfile(
        name="Synthetic profile",
        profile_pin="4242",
        sex=Sex.FEMALE,
        date_of_birth=birth_date,
        height_cm=175,
        athlete_mode=False,
    )


def _runtime() -> ScaleRuntime:
    profile = _profile()
    return ScaleRuntime("scale-1", profiles={profile.profile_pin: profile})


def _body(response: object) -> dict[str, object]:
    return json.loads(response.text)  # type: ignore[attr-defined]


def test_valid_measurement_and_retry_statuses() -> None:
    runtime = _runtime()

    accepted = asyncio.run(
        async_handle_measurement(runtime, FakeRequest(_payload()))  # type: ignore[arg-type]
    )
    duplicate = asyncio.run(
        async_handle_measurement(runtime, FakeRequest(_payload()))  # type: ignore[arg-type]
    )

    assert accepted.status == 202
    assert _body(accepted)["status"] == "accepted"
    assert duplicate.status == 200
    assert _body(duplicate)["status"] == "duplicate"
    assert runtime.last_processed_measurement is not None
    assert runtime.last_processed_measurement.result.bmr_kcal == 1455


def test_accepted_and_duplicate_deliveries_persist_runtime_state() -> None:
    runtime = _runtime()
    saved_ids: list[tuple[str, ...]] = []
    published_ids: list[str] = []
    runtime.add_listener(
        lambda processed: published_ids.append(processed.measurement.measurement_id)
    )

    async def save_state(state: ScaleRuntime) -> None:
        saved_ids.append(state.seen_measurement_ids)

    accepted = asyncio.run(
        async_handle_measurement(
            runtime,
            FakeRequest(_payload()),  # type: ignore[arg-type]
            save_state,
        )
    )
    duplicate = asyncio.run(
        async_handle_measurement(
            runtime,
            FakeRequest(_payload()),  # type: ignore[arg-type]
            save_state,
        )
    )

    assert accepted.status == 202
    assert duplicate.status == 200
    assert saved_ids == [("measurement-1",), ("measurement-1",)]
    assert published_ids == ["measurement-1"]


def test_failed_persistence_does_not_publish_sensor_state() -> None:
    runtime = _runtime()
    published_ids: list[str] = []
    runtime.add_listener(
        lambda processed: published_ids.append(processed.measurement.measurement_id)
    )

    async def fail_save(state: ScaleRuntime) -> None:
        del state
        raise RuntimeError("storage unavailable")

    with pytest.raises(RuntimeError, match="storage unavailable"):
        asyncio.run(
            async_handle_measurement(
                runtime,
                FakeRequest(_payload()),  # type: ignore[arg-type]
                fail_save,
            )
        )

    assert published_ids == []


def test_unknown_profile_returns_conflict_without_consuming_measurement_id() -> None:
    runtime = ScaleRuntime("scale-1")

    unknown = asyncio.run(
        async_handle_measurement(runtime, FakeRequest(_payload()))  # type: ignore[arg-type]
    )
    assert unknown.status == 409
    assert _body(unknown) == {
        "status": "error",
        "error": "unknown_profile",
        "profile_pin": "4242",
    }
    assert runtime.seen_count == 0

    profile = _profile()
    runtime.profiles[profile.profile_pin] = profile
    accepted = asyncio.run(
        async_handle_measurement(runtime, FakeRequest(_payload()))  # type: ignore[arg-type]
    )
    assert accepted.status == 202
    assert runtime.seen_count == 1


def test_rejected_measurement_is_not_persisted() -> None:
    runtime = ScaleRuntime("scale-1")
    save_count = 0

    async def save_state(state: ScaleRuntime) -> None:
        nonlocal save_count
        del state
        save_count += 1

    response = asyncio.run(
        async_handle_measurement(
            runtime,
            FakeRequest(_payload()),  # type: ignore[arg-type]
            save_state,
        )
    )

    assert response.status == 409
    assert save_count == 0


def test_profile_invalid_for_measurement_returns_conflict_without_commit() -> None:
    profile = _profile(birth_date=date(2026, 1, 16))
    runtime = ScaleRuntime("scale-1", profiles={profile.profile_pin: profile})

    response = asyncio.run(
        async_handle_measurement(runtime, FakeRequest(_payload()))  # type: ignore[arg-type]
    )
    assert response.status == 409
    assert _body(response)["error"] == "profile_invalid_for_measurement"
    assert runtime.seen_count == 0
    assert runtime.last_processed_measurement is None


def test_invalid_json_returns_bad_request() -> None:
    error = json.JSONDecodeError("invalid", "{", 1)
    response = asyncio.run(
        async_handle_measurement(
            ScaleRuntime("scale-1"),
            FakeRequest(error=error),  # type: ignore[arg-type]
        )
    )
    assert response.status == 400
    assert _body(response)["error"] == "invalid_json"


def test_wrong_scale_returns_unprocessable_entity() -> None:
    response = asyncio.run(
        async_handle_measurement(
            ScaleRuntime("another-scale"),
            FakeRequest(_payload()),  # type: ignore[arg-type]
        )
    )
    assert response.status == 422
    assert _body(response)["error"] == "invalid_measurement"
