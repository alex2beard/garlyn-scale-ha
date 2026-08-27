"""Tests for the restart-safe optional SparkyFitness delivery path."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

import pytest
from aiohttp import ClientConnectionError

from custom_components.garlyn_scale.algorithm import Sex
from custom_components.garlyn_scale.models import UserProfile
from custom_components.garlyn_scale.runtime import ScaleRuntime
from custom_components.garlyn_scale.sparky import (
    SparkyClient,
    SparkyOutbox,
    SparkyQueueItem,
    SparkySendResult,
    SparkySyncManager,
    normalize_sparky_url,
)
from custom_components.garlyn_scale.transport import parse_measurement

EXPECTED_TYPES = [
    "weight",
    "body_fat_percentage",
    "body_fat_mass_kg",
    "muscle_percentage",
    "muscle_mass_kg",
    "body_water_percentage",
    "body_water_mass_kg",
]


def _profile(
    *,
    sparky_enabled: bool = True,
    api_key: str | None = "secret-token",
) -> UserProfile:
    return UserProfile(
        name="Synthetic profile",
        profile_pin="4242",
        profile_id="a" * 32,
        sex=Sex.FEMALE,
        date_of_birth=date(1991, 6, 15),
        height_cm=175,
        athlete_mode=False,
        sparky_enabled=sparky_enabled,
        sparky_api_key=api_key,
    )


def _processed(
    measurement_id: str = "measurement-1",
    measured_at: str = "2026-01-15T23:30:00+00:00",
):
    profile = _profile()
    runtime = ScaleRuntime("scale-1", profiles={profile.profile_pin: profile})
    runtime.process(
        parse_measurement(
            {
                "protocol_version": 1,
                "scale_id": "scale-1",
                "measurement_id": measurement_id,
                "measured_at": measured_at,
                "profile_pin": "4242",
                "weight_kg": 74.8,
                "bia": {
                    "20khz": [410.2, 408.6, 360.4, 355.9, 30.1],
                    "100khz": [365.1, 363.8, 315.6, 312.2, 26.5],
                },
            }
        )
    )
    assert runtime.last_processed_measurement is not None
    return runtime.last_processed_measurement


def _item(
    measurement_id: str = "measurement-1",
    measured_at: str = "2026-01-15T23:30:00+00:00",
    *,
    timezone_name: str = "UTC",
) -> SparkyQueueItem:
    return SparkyQueueItem.from_processed(
        _processed(measurement_id, measured_at),
        timezone_name=timezone_name,
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sparky.example.com",
        "ftp://sparky.example.com",
        "https://user:password@sparky.example.com",
        "https://sparky.example.com?secret=value",
        "https://sparky.example.com#fragment",
        "https://sparky example.com",
    ],
)
def test_sparky_url_rejects_unsafe_or_incomplete_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_sparky_url(value)


def test_sparky_url_normalizes_whitespace_scheme_and_trailing_slash() -> None:
    assert (
        normalize_sparky_url(" HTTPS://sparky.example.com/deployment/ ")
        == "https://sparky.example.com/deployment"
    )


def test_payload_rounds_exactly_the_seven_agreed_values_for_sparky() -> None:
    item = _item(timezone_name="Europe/Amsterdam")

    payload = item.payload()

    assert item.entry_date == "2026-01-16"
    assert item.measured_at == "2026-01-15T23:30:00Z"
    assert [record["type"] for record in payload] == EXPECTED_TYPES
    assert [record["unit"] for record in payload] == [
        "kg",
        "%",
        "kg",
        "%",
        "kg",
        "%",
        "kg",
    ]
    assert [record["value"] for record in payload] == [
        74.8,
        32.8,
        24.54,
        62.37,
        46.65,
        49.13,
        36.75,
    ]
    assert item.values.body_fat_mass_kg == pytest.approx(24.537979125976562)
    assert item.as_dict()["values"] == item.values.as_dict()
    assert all(record["date"] == "2026-01-16" for record in payload)
    assert all(record["timestamp"] == "2026-01-15T23:30:00Z" for record in payload)
    assert all(record["record_timezone"] == "Europe/Amsterdam" for record in payload)
    assert [record["source"] for record in payload] == [
        "GARLYN Scale via Home Assistant",
        "GARLYN Scale via Home Assistant",
        "manual",
        "manual",
        "GARLYN Scale via Home Assistant",
        "GARLYN Scale via Home Assistant",
        "manual",
    ]
    serialized = str(payload)
    assert "secret-token" not in serialized
    assert "4242" not in serialized
    assert "20khz" not in serialized
    assert "bmr" not in serialized.lower()
    assert "bmi" not in serialized.lower()


def test_outbox_coalesces_pending_measurements_and_remembers_newest_day_value() -> None:
    outbox = SparkyOutbox()
    first = _item("first", "2026-01-15T08:00:00+00:00")
    newest = _item("newest", "2026-01-15T09:00:00+00:00")
    stale = _item("stale", "2026-01-15T08:30:00+00:00")

    assert outbox.enqueue(first) is True
    assert outbox.enqueue(newest) is True
    assert [item.measurement_id for item in outbox.items] == ["newest"]
    assert outbox.enqueue(stale) is False

    assert outbox.acknowledge("newest") is True
    assert outbox.pending_count == 0
    restored = SparkyOutbox.from_dict(outbox.as_dict())
    assert restored.enqueue(stale) is False
    assert restored.pending_count == 0

    next_day = _item("next-day", "2026-01-16T08:00:00+00:00")
    assert restored.enqueue(next_day) is True
    assert restored.items == (next_day,)


def test_outbox_evicts_oldest_work_instead_of_blocking_ha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "custom_components.garlyn_scale.sparky.MAX_SPARKY_OUTBOX_ITEMS", 2
    )
    outbox = SparkyOutbox()
    for measurement_id, timestamp in (
        ("one", "2026-01-01T08:00:00+00:00"),
        ("two", "2026-01-02T08:00:00+00:00"),
        ("three", "2026-01-03T08:00:00+00:00"),
    ):
        assert outbox.enqueue(_item(measurement_id, timestamp)) is True

    assert [item.measurement_id for item in outbox.items] == ["two", "three"]


def test_outbox_retry_state_round_trips_without_credentials() -> None:
    outbox = SparkyOutbox((_item(),))
    now = datetime(2026, 1, 16, 0, 0, tzinfo=UTC)

    first_failure = outbox.mark_failed("measurement-1", error_code="http_503", now=now)
    assert first_failure is not None
    assert first_failure.attempts == 1
    assert first_failure.next_attempt_at == "2026-01-16T00:01:00Z"
    assert outbox.next_item(now) == (first_failure, 60.0)

    second_failure = outbox.mark_failed(
        "measurement-1",
        error_code="timeout",
        now=datetime(2026, 1, 16, 0, 1, tzinfo=UTC),
    )
    assert second_failure is not None
    assert second_failure.attempts == 2
    assert second_failure.next_attempt_at == "2026-01-16T00:03:00Z"

    stored = outbox.as_dict()
    assert "secret-token" not in str(stored)
    restored = SparkyOutbox.from_dict(stored)
    assert restored.items == outbox.items
    assert restored.as_dict() == stored


class _FakeResponse:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self.body = body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def json(self, *, content_type: object = None) -> object:
        del content_type
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class _FakeSession:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def post(self, endpoint: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append({"endpoint": endpoint, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _successful_response() -> dict[str, object]:
    return {
        "message": "All health data successfully processed.",
        "processed": [
            {"type": field_type, "status": "success", "data": {}}
            for field_type in reversed(EXPECTED_TYPES)
        ],
        "errors": [],
        "skipped": [],
    }


def test_client_sends_bearer_batch_and_requires_all_seven_successes() -> None:
    session = _FakeSession(_FakeResponse(200, _successful_response()))
    client = SparkyClient(session, "https://sparky.example.com/base/")  # type: ignore[arg-type]
    item = _item()

    result = asyncio.run(client.async_send(item, "secret-token"))

    assert result == SparkySendResult(True, http_status=200)
    assert len(session.requests) == 1
    request = session.requests[0]
    assert request["endpoint"] == ("https://sparky.example.com/base/api/health-data")
    assert request["headers"] == {
        "Authorization": "Bearer secret-token",
        "Content-Type": "application/json",
    }
    assert request["json"] == item.payload()
    assert request["timeout"].total == 15


@pytest.mark.parametrize(
    ("response", "error_code"),
    [
        (_FakeResponse(401, {}), "http_401"),
        (
            _FakeResponse(
                200,
                {
                    "processed": [],
                    "errors": [{"error": "invalid"}],
                    "skipped": [],
                },
            ),
            "partial_failure",
        ),
        (
            _FakeResponse(
                200,
                {
                    "processed": [
                        {"type": field_type, "status": "success"}
                        for field_type in EXPECTED_TYPES[:-1]
                    ],
                    "errors": [],
                    "skipped": [],
                },
            ),
            "incomplete_response",
        ),
        (_FakeResponse(200, ValueError("not JSON")), "invalid_json"),
        (ClientConnectionError("offline"), "client_error"),
    ],
)
def test_client_retries_every_non_atomic_or_transport_result(
    response: _FakeResponse | Exception,
    error_code: str,
) -> None:
    client = SparkyClient(_FakeSession(response), "https://sparky.example.com")  # type: ignore[arg-type]

    result = asyncio.run(client.async_send(_item(), "secret-token"))

    assert result.success is False
    assert result.error_code == error_code


class _SuccessfulSender:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def async_send(self, item: SparkyQueueItem, api_key: str) -> SparkySendResult:
        del item
        self.keys.append(api_key)
        return SparkySendResult(True, http_status=200)


def test_sync_manager_acknowledges_in_background_and_persists_queue() -> None:
    async def scenario() -> None:
        profile = _profile()
        outbox = SparkyOutbox((_item(),))
        sender = _SuccessfulSender()
        saved_pending_counts: list[int] = []
        tasks: list[asyncio.Task[None]] = []

        async def save_state() -> None:
            saved_pending_counts.append(outbox.pending_count)

        def create_task(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        manager = SparkySyncManager(
            outbox=outbox,
            profiles={profile.profile_pin: profile},
            client=sender,  # type: ignore[arg-type]
            state_lock=asyncio.Lock(),
            async_save_state=save_state,
            task_factory=create_task,
            now=lambda: datetime(2026, 1, 16, 0, 0, tzinfo=UTC),
        )

        manager.wake()
        await tasks[0]
        await asyncio.sleep(0)
        assert sender.keys == ["secret-token"]
        assert outbox.pending_count == 0
        assert saved_pending_counts == [0]
        await manager.async_stop()

    asyncio.run(scenario())
