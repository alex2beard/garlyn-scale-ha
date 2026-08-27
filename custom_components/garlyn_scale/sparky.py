"""Restart-safe optional delivery of GARLYN measurements to SparkyFitness."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    MAX_SPARKY_DAY_WATERMARKS,
    MAX_SPARKY_OUTBOX_ITEMS,
    SPARKY_HEALTH_DATA_PATH,
    SPARKY_REQUEST_TIMEOUT_SECONDS,
    SPARKY_RETRY_INITIAL_SECONDS,
    SPARKY_RETRY_MAX_SECONDS,
    SPARKY_SOURCE,
)
from .models import UserProfile
from .runtime import ProcessedMeasurement

_LOGGER = logging.getLogger(__name__)

_VALUE_KEYS = (
    "weight",
    "body_fat_percentage",
    "body_fat_mass_kg",
    "muscle_percentage",
    "muscle_mass_kg",
    "body_water_percentage",
    "body_water_mass_kg",
)
_VALUE_UNITS = {
    "weight": "kg",
    "body_fat_percentage": "%",
    "body_fat_mass_kg": "kg",
    "muscle_percentage": "%",
    "muscle_mass_kg": "kg",
    "body_water_percentage": "%",
    "body_water_mass_kg": "kg",
}
_QUEUE_ITEM_KEYS = frozenset(
    {
        "measurement_id",
        "profile_id",
        "entry_date",
        "measured_at",
        "record_timezone",
        "values",
        "attempts",
        "next_attempt_at",
        "last_error",
    }
)
_OUTBOX_KEYS = frozenset({"items", "latest_by_day"})
_DAY_WATERMARK_KEYS = frozenset({"profile_id", "entry_date", "measured_at"})


def normalize_sparky_url(value: object) -> str:
    """Validate and normalize a SparkyFitness base URL."""
    if not isinstance(value, str):
        raise ValueError("Sparky URL must be a string")
    raw = value.strip()
    if not raw or any(character.isspace() for character in raw):
        raise ValueError("Sparky URL must not be empty")
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as err:
        raise ValueError("Sparky URL is invalid") from err
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Sparky URL must be an HTTP(S) base URL")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path.rstrip("/"),
            "",
            "",
        )
    )


def _bounded_string(value: object, path: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(
            f"{path} must be non-empty, trimmed, and at most {maximum} characters"
        )
    return value


def _bounded_number(
    value: object,
    path: str,
    maximum: float,
    *,
    allow_zero: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{path} must be a number")
    converted = float(value)
    lower_bound_ok = converted >= 0 if allow_zero else converted > 0
    if not math.isfinite(converted) or not lower_bound_ok or converted > maximum:
        interval = f"[0, {maximum}]" if allow_zero else f"(0, {maximum}]"
        raise ValueError(f"{path} must be finite and in {interval}")
    return converted


def _aware_datetime(value: object, path: str) -> datetime:
    raw = _bounded_string(value, path, 64)
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as err:
        raise ValueError(f"{path} must be ISO 8601") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{path} must include a UTC offset")
    return parsed


@dataclass(frozen=True, slots=True)
class SparkyValues:
    """The seven agreed values sent for one physical measurement."""

    weight: float
    body_fat_percentage: float
    body_fat_mass_kg: float
    muscle_percentage: float
    muscle_mass_kg: float
    body_water_percentage: float
    body_water_mass_kg: float

    def __post_init__(self) -> None:
        for key in _VALUE_KEYS:
            maximum = 100.0 if key.endswith("percentage") else 500.0
            object.__setattr__(
                self,
                key,
                _bounded_number(
                    getattr(self, key),
                    f"values.{key}",
                    maximum,
                    allow_zero=key != "weight",
                ),
            )

    @classmethod
    def from_processed(cls, processed: ProcessedMeasurement) -> SparkyValues:
        """Copy the agreed native algorithm outputs into an immutable value set."""
        result = processed.result
        return cls(
            weight=processed.measurement.weight_kg,
            body_fat_percentage=result.body_fat_pct,
            body_fat_mass_kg=result.body_fat_kg,
            muscle_percentage=result.muscle_pct,
            muscle_mass_kg=result.muscle_kg,
            body_water_percentage=result.body_water_pct,
            body_water_mass_kg=result.body_water_kg,
        )

    def as_dict(self) -> dict[str, float]:
        """Return values in the stable API field order."""
        return {key: getattr(self, key) for key in _VALUE_KEYS}


@dataclass(frozen=True, slots=True)
class SparkyQueueItem:
    """One restart-safe daily Sparky update without credentials or raw BIA."""

    measurement_id: str
    profile_id: str
    entry_date: str
    measured_at: str
    record_timezone: str
    values: SparkyValues
    attempts: int = 0
    next_attempt_at: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        _bounded_string(self.measurement_id, "measurement_id", 128)
        profile_id = _bounded_string(self.profile_id, "profile_id", 32)
        if len(profile_id) != 32 or any(
            character not in "0123456789abcdef" for character in profile_id
        ):
            raise ValueError("profile_id must contain 32 lowercase hexadecimal digits")
        try:
            date.fromisoformat(self.entry_date)
        except (TypeError, ValueError) as err:
            raise ValueError("entry_date must be an ISO 8601 date") from err
        _aware_datetime(self.measured_at, "measured_at")
        timezone_name = _bounded_string(self.record_timezone, "record_timezone", 64)
        try:
            ZoneInfo(timezone_name)
        except (KeyError, ValueError) as err:
            raise ValueError("record_timezone must be an IANA timezone") from err
        if type(self.attempts) is not int or not 0 <= self.attempts <= 1_000_000:
            raise ValueError("attempts must be a non-negative integer")
        if self.next_attempt_at is not None:
            _aware_datetime(self.next_attempt_at, "next_attempt_at")
        if self.last_error is not None:
            _bounded_string(self.last_error, "last_error", 64)

    @classmethod
    def from_processed(
        cls,
        processed: ProcessedMeasurement,
        *,
        timezone_name: str,
    ) -> SparkyQueueItem:
        """Build a queue item using Home Assistant's timezone for the daily row."""
        measured_at = processed.measurement.measured_at
        local_date = measured_at.astimezone(ZoneInfo(timezone_name)).date()
        utc_timestamp = measured_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        assert processed.user_profile.profile_id is not None
        return cls(
            measurement_id=processed.measurement.measurement_id,
            profile_id=processed.user_profile.profile_id,
            entry_date=local_date.isoformat(),
            measured_at=utc_timestamp,
            record_timezone=timezone_name,
            values=SparkyValues.from_processed(processed),
        )

    @property
    def measured_datetime(self) -> datetime:
        """Return the physical measurement instant."""
        return _aware_datetime(self.measured_at, "measured_at")

    @property
    def next_attempt_datetime(self) -> datetime | None:
        """Return the persisted retry instant."""
        if self.next_attempt_at is None:
            return None
        return _aware_datetime(self.next_attempt_at, "next_attempt_at")

    def payload(self) -> list[dict[str, str | float]]:
        """Build the seven-record Sparky health-data request body."""
        common = {
            "date": self.entry_date,
            "timestamp": self.measured_at,
            "record_timezone": self.record_timezone,
            "source": SPARKY_SOURCE,
        }
        return [
            {
                "type": field_name,
                "value": value,
                "unit": _VALUE_UNITS[field_name],
                **common,
            }
            for field_name, value in self.values.as_dict().items()
        ]

    def as_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-safe queue representation."""
        return {
            "measurement_id": self.measurement_id,
            "profile_id": self.profile_id,
            "entry_date": self.entry_date,
            "measured_at": self.measured_at,
            "record_timezone": self.record_timezone,
            "values": self.values.as_dict(),
            "attempts": self.attempts,
            "next_attempt_at": self.next_attempt_at,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, value: object) -> SparkyQueueItem:
        """Strictly restore one queue item from private HA storage."""
        if not isinstance(value, Mapping) or frozenset(value) != _QUEUE_ITEM_KEYS:
            raise ValueError("Sparky queue item has invalid stored fields")
        raw_values = value["values"]
        if not isinstance(raw_values, Mapping) or frozenset(raw_values) != frozenset(
            _VALUE_KEYS
        ):
            raise ValueError("Sparky queue values have invalid stored fields")
        return cls(
            measurement_id=value["measurement_id"],  # type: ignore[arg-type]
            profile_id=value["profile_id"],  # type: ignore[arg-type]
            entry_date=value["entry_date"],  # type: ignore[arg-type]
            measured_at=value["measured_at"],  # type: ignore[arg-type]
            record_timezone=value["record_timezone"],  # type: ignore[arg-type]
            values=SparkyValues(**raw_values),  # type: ignore[arg-type]
            attempts=value["attempts"],  # type: ignore[arg-type]
            next_attempt_at=value["next_attempt_at"],  # type: ignore[arg-type]
            last_error=value["last_error"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class _SparkyDayWatermark:
    """Newest accepted measurement instant for one profile and local day."""

    profile_id: str
    entry_date: str
    measured_at: str

    def __post_init__(self) -> None:
        profile_id = _bounded_string(self.profile_id, "profile_id", 32)
        if len(profile_id) != 32 or any(
            character not in "0123456789abcdef" for character in profile_id
        ):
            raise ValueError("profile_id must contain 32 lowercase hexadecimal digits")
        try:
            date.fromisoformat(self.entry_date)
        except (TypeError, ValueError) as err:
            raise ValueError("entry_date must be an ISO 8601 date") from err
        _aware_datetime(self.measured_at, "measured_at")

    @property
    def key(self) -> tuple[str, str]:
        return self.profile_id, self.entry_date

    @property
    def measured_datetime(self) -> datetime:
        return _aware_datetime(self.measured_at, "measured_at")

    def as_dict(self) -> dict[str, str]:
        return {
            "profile_id": self.profile_id,
            "entry_date": self.entry_date,
            "measured_at": self.measured_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> _SparkyDayWatermark:
        if not isinstance(value, Mapping) or frozenset(value) != _DAY_WATERMARK_KEYS:
            raise ValueError("Sparky day watermark has invalid stored fields")
        return cls(
            profile_id=value["profile_id"],  # type: ignore[arg-type]
            entry_date=value["entry_date"],  # type: ignore[arg-type]
            measured_at=value["measured_at"],  # type: ignore[arg-type]
        )


type _OutboxCheckpoint = tuple[
    tuple[SparkyQueueItem, ...], tuple[_SparkyDayWatermark, ...]
]


class SparkyOutbox:
    """Ordered, coalescing queue persisted together with accepted HA state."""

    def __init__(
        self,
        items: Sequence[SparkyQueueItem] = (),
        latest_by_day: Sequence[_SparkyDayWatermark] = (),
    ) -> None:
        if len(items) > MAX_SPARKY_OUTBOX_ITEMS:
            raise ValueError("too many stored Sparky queue items")
        self._items: OrderedDict[str, SparkyQueueItem] = OrderedDict()
        for item in items:
            if not isinstance(item, SparkyQueueItem):
                raise TypeError("Sparky outbox must contain SparkyQueueItem values")
            if item.measurement_id in self._items:
                raise ValueError("stored Sparky measurement IDs must be unique")
            self._items[item.measurement_id] = item
        if len(latest_by_day) > MAX_SPARKY_DAY_WATERMARKS:
            raise ValueError("too many stored Sparky day watermarks")
        self._latest_by_day: OrderedDict[tuple[str, str], _SparkyDayWatermark] = (
            OrderedDict()
        )
        for watermark in latest_by_day:
            if not isinstance(watermark, _SparkyDayWatermark):
                raise TypeError(
                    "Sparky day watermarks must contain valid watermark values"
                )
            if watermark.key in self._latest_by_day:
                raise ValueError("stored Sparky day watermarks must be unique")
            self._latest_by_day[watermark.key] = watermark
        for item in self._items.values():
            key = (item.profile_id, item.entry_date)
            watermark = self._latest_by_day.get(key)
            if watermark is None or (
                watermark.measured_datetime < item.measured_datetime
            ):
                self._set_watermark(item)

    @property
    def pending_count(self) -> int:
        """Return the number of pending daily updates."""
        return len(self._items)

    @property
    def items(self) -> tuple[SparkyQueueItem, ...]:
        """Return an immutable oldest-first queue snapshot."""
        return tuple(self._items.values())

    def enqueue(self, item: SparkyQueueItem) -> bool:
        """Queue the newest measurement for one profile and local date."""
        if not isinstance(item, SparkyQueueItem):
            raise TypeError("item must be a SparkyQueueItem")
        if item.measurement_id in self._items:
            return False

        day_key = (item.profile_id, item.entry_date)
        watermark = self._latest_by_day.get(day_key)
        if watermark is not None and (
            watermark.measured_datetime > item.measured_datetime
        ):
            return False

        for queued_id, queued in tuple(self._items.items()):
            if (
                queued.profile_id == item.profile_id
                and queued.entry_date == item.entry_date
            ):
                if queued.measured_datetime > item.measured_datetime:
                    return False
                del self._items[queued_id]

        self._set_watermark(item)
        if len(self._items) >= MAX_SPARKY_OUTBOX_ITEMS:
            self._items.popitem(last=False)
            _LOGGER.warning(
                "Sparky outbox reached its limit; dropped the oldest pending day"
            )
        self._items[item.measurement_id] = item
        return True

    def _set_watermark(self, item: SparkyQueueItem) -> None:
        watermark = _SparkyDayWatermark(
            profile_id=item.profile_id,
            entry_date=item.entry_date,
            measured_at=item.measured_at,
        )
        self._latest_by_day[watermark.key] = watermark
        self._latest_by_day.move_to_end(watermark.key)
        while len(self._latest_by_day) > MAX_SPARKY_DAY_WATERMARKS:
            self._latest_by_day.popitem(last=False)

    def get(self, measurement_id: str) -> SparkyQueueItem | None:
        """Return a queued item by physical measurement ID."""
        return self._items.get(measurement_id)

    def acknowledge(self, measurement_id: str) -> bool:
        """Remove an acknowledged item."""
        return self._items.pop(measurement_id, None) is not None

    def mark_failed(
        self,
        measurement_id: str,
        *,
        error_code: str,
        now: datetime,
    ) -> SparkyQueueItem | None:
        """Persist an exponentially delayed retry for one item."""
        item = self._items.get(measurement_id)
        if item is None:
            return None
        attempts = item.attempts + 1
        exponent = min(attempts - 1, 30)
        retry_seconds = min(
            SPARKY_RETRY_INITIAL_SECONDS * (2**exponent),
            SPARKY_RETRY_MAX_SECONDS,
        )
        retry_at = now.astimezone(UTC) + timedelta(seconds=retry_seconds)
        failed = replace(
            item,
            attempts=attempts,
            next_attempt_at=retry_at.isoformat().replace("+00:00", "Z"),
            last_error=_bounded_string(error_code, "error_code", 64),
        )
        self._items[measurement_id] = failed
        return failed

    def next_item(self, now: datetime) -> tuple[SparkyQueueItem | None, float]:
        """Return the earliest eligible item and seconds until it is due."""
        if not self._items:
            return None, 0.0
        utc_now = now.astimezone(UTC)

        def due_at(item: SparkyQueueItem) -> datetime:
            return item.next_attempt_datetime or datetime.min.replace(tzinfo=UTC)

        item = min(self._items.values(), key=due_at)
        delay = max(0.0, (due_at(item) - utc_now).total_seconds())
        return item, delay

    def prune(self, profiles: Mapping[str, UserProfile]) -> bool:
        """Drop queued work for profiles whose sync was disabled or removed."""
        enabled_ids = {
            profile.profile_id
            for profile in profiles.values()
            if profile.sparky_enabled and profile.sparky_api_key is not None
        }
        removed = False
        for measurement_id, item in tuple(self._items.items()):
            if item.profile_id not in enabled_ids:
                del self._items[measurement_id]
                removed = True
        for key, watermark in tuple(self._latest_by_day.items()):
            if watermark.profile_id not in enabled_ids:
                del self._latest_by_day[key]
                removed = True
        return removed

    def checkpoint(self) -> _OutboxCheckpoint:
        """Capture the small mutable portion for transactional rollback."""
        return tuple(self._items.values()), tuple(self._latest_by_day.values())

    def restore_checkpoint(self, checkpoint: _OutboxCheckpoint) -> None:
        """Restore an internal checkpoint after HA persistence failed."""
        items, latest_by_day = checkpoint
        restored = SparkyOutbox(items, latest_by_day)
        self._items = restored._items
        self._latest_by_day = restored._latest_by_day

    def as_dict(self) -> dict[str, object]:
        """Return the JSON-safe outbox and latest-per-day watermarks."""
        return {
            "items": [item.as_dict() for item in self._items.values()],
            "latest_by_day": [
                watermark.as_dict() for watermark in self._latest_by_day.values()
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> SparkyOutbox:
        """Strictly restore a private HA outbox.

        An item list is accepted as a defensive bridge for unreleased development
        snapshots written before latest-per-day watermarks were introduced.
        """
        if isinstance(value, list):
            return cls(tuple(SparkyQueueItem.from_dict(item) for item in value))
        if not isinstance(value, Mapping) or frozenset(value) != _OUTBOX_KEYS:
            raise ValueError("Sparky outbox has invalid stored fields")
        raw_items = value["items"]
        raw_latest_by_day = value["latest_by_day"]
        if not isinstance(raw_items, list) or not isinstance(raw_latest_by_day, list):
            raise ValueError("Sparky outbox entries must be lists")
        return cls(
            tuple(SparkyQueueItem.from_dict(item) for item in raw_items),
            tuple(
                _SparkyDayWatermark.from_dict(watermark)
                for watermark in raw_latest_by_day
            ),
        )


@dataclass(frozen=True, slots=True)
class SparkySendResult:
    """Sanitized result suitable for retry state and logs."""

    success: bool
    error_code: str | None = None
    http_status: int | None = None


class SparkyClient:
    """Minimal API-key client for SparkyFitness health-data ingestion."""

    def __init__(self, session: ClientSession, base_url: str) -> None:
        self._session = session
        self._endpoint = f"{normalize_sparky_url(base_url)}{SPARKY_HEALTH_DATA_PATH}"

    async def async_send(self, item: SparkyQueueItem, api_key: str) -> SparkySendResult:
        """Send all seven records and verify Sparky's per-record result body."""
        try:
            async with self._session.post(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=item.payload(),
                timeout=ClientTimeout(total=SPARKY_REQUEST_TIMEOUT_SECONDS),
            ) as response:
                if response.status != 200:
                    return SparkySendResult(
                        False,
                        f"http_{response.status}",
                        response.status,
                    )
                try:
                    body = await response.json(content_type=None)
                except (TypeError, ValueError, UnicodeDecodeError):
                    return SparkySendResult(False, "invalid_json", response.status)
        except TimeoutError:
            return SparkySendResult(False, "timeout")
        except ClientError:
            return SparkySendResult(False, "client_error")

        if not isinstance(body, Mapping):
            return SparkySendResult(False, "invalid_response", 200)
        processed = body.get("processed")
        errors = body.get("errors")
        skipped = body.get("skipped")
        if not isinstance(processed, list) or errors != [] or skipped != []:
            return SparkySendResult(False, "partial_failure", 200)
        processed_types: list[str] = []
        for result in processed:
            if (
                not isinstance(result, Mapping)
                or result.get("status") != "success"
                or not isinstance(result.get("type"), str)
            ):
                return SparkySendResult(False, "invalid_response", 200)
            processed_types.append(result["type"])
        if Counter(processed_types) != Counter(_VALUE_KEYS):
            return SparkySendResult(False, "incomplete_response", 200)
        return SparkySendResult(True, http_status=200)


type TaskFactory = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]
type SaveStateCallback = Callable[[], Awaitable[None]]


class SparkySyncManager:
    """Deliver a persistent outbox without blocking ESP webhook acknowledgements."""

    def __init__(
        self,
        *,
        outbox: SparkyOutbox,
        profiles: Mapping[str, UserProfile],
        client: SparkyClient,
        state_lock: asyncio.Lock,
        async_save_state: SaveStateCallback,
        task_factory: TaskFactory,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._outbox = outbox
        self._profiles = profiles
        self._client = client
        self._state_lock = state_lock
        self._async_save_state = async_save_state
        self._task_factory = task_factory
        self._now = now or (lambda: datetime.now(UTC))
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def wake(self) -> None:
        """Wake or start the non-blocking delivery worker."""
        if self._stopping or self._outbox.pending_count == 0:
            return
        self._wake_event.set()
        if self._task is None or self._task.done():
            self._task = self._task_factory(self._async_run())
            self._task.add_done_callback(self._handle_task_done)

    async def async_stop(self) -> None:
        """Stop the worker during config-entry unload."""
        self._stopping = True
        self._wake_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    def _handle_task_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOGGER.error(
                "Unexpected Sparky sync worker failure (%s)",
                type(error).__name__,
            )
        if self._task is task:
            self._task = None
        if not self._stopping and self._outbox.pending_count:
            self.wake()

    def _profile_for(self, item: SparkyQueueItem) -> UserProfile | None:
        return next(
            (
                profile
                for profile in self._profiles.values()
                if profile.profile_id == item.profile_id
            ),
            None,
        )

    async def _async_persist_queue_change(self) -> None:
        try:
            await self._async_save_state()
        except Exception:
            _LOGGER.exception("Unable to persist the GARLYN Sparky outbox")

    async def _async_run(self) -> None:
        while not self._stopping:
            self._wake_event.clear()
            async with self._state_lock:
                item, delay = self._outbox.next_item(self._now())
            if item is None:
                return
            if delay > 0:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake_event.wait(), timeout=delay)
                continue

            profile = self._profile_for(item)
            if (
                profile is None
                or not profile.sparky_enabled
                or profile.sparky_api_key is None
            ):
                async with self._state_lock:
                    self._outbox.acknowledge(item.measurement_id)
                    await self._async_persist_queue_change()
                continue

            result = await self._client.async_send(item, profile.sparky_api_key)
            async with self._state_lock:
                if self._outbox.get(item.measurement_id) is None:
                    continue
                if result.success:
                    self._outbox.acknowledge(item.measurement_id)
                else:
                    failed = self._outbox.mark_failed(
                        item.measurement_id,
                        error_code=result.error_code or "unknown_error",
                        now=self._now(),
                    )
                await self._async_persist_queue_change()

            if result.success:
                _LOGGER.debug(
                    "Sparky accepted all seven GARLYN health values; pending=%d",
                    self._outbox.pending_count,
                )
            else:
                assert failed is not None
                _LOGGER.warning(
                    "Sparky sync failed (%s); attempt=%d, pending=%d",
                    result.error_code,
                    failed.attempts,
                    self._outbox.pending_count,
                )
