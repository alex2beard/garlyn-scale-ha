"""Push runtime state without polling or a DataUpdateCoordinator."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .algorithm import (
    ALGORITHM_VERSION,
    AlgorithmProfile,
    BodyCompositionResult,
    calculate_body_composition,
)
from .const import DEFAULT_DEDUPLICATION_CACHE_SIZE
from .models import UserProfile
from .transport import Measurement

_LOGGER = logging.getLogger(__name__)


class AcceptanceStatus(StrEnum):
    """Result of accepting one validated measurement."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class UnknownProfileError(LookupError):
    """Raised when no configured profile owns a measurement PIN."""

    def __init__(self, profile_pin: str) -> None:
        super().__init__(f"no configured profile for PIN {profile_pin}")
        self.profile_pin = profile_pin


class InvalidProfileForMeasurementError(ValueError):
    """Raised when a profile cannot be used at the measurement timestamp."""

    def __init__(self, profile_pin: str) -> None:
        super().__init__(f"profile {profile_pin} is invalid for this measurement")
        self.profile_pin = profile_pin


@dataclass(frozen=True, slots=True)
class ProcessedMeasurement:
    """One accepted measurement and its immutable calculated snapshot."""

    measurement: Measurement
    user_profile: UserProfile
    algorithm_profile: AlgorithmProfile
    result: BodyCompositionResult
    algorithm_version: str = ALGORITHM_VERSION


@dataclass(frozen=True, slots=True)
class RuntimeCheckpoint:
    """Internal in-memory snapshot used while persisting an acceptance."""

    seen_measurement_ids: tuple[str, ...]
    latest_by_profile: dict[str, ProcessedMeasurement]
    last_measurement: Measurement | None
    last_processed_measurement: ProcessedMeasurement | None
    published_measurement_id: str | None


type MeasurementListener = Callable[[ProcessedMeasurement], None]


@dataclass(slots=True)
class ScaleRuntime:
    """Per-config-entry runtime state for a single physical scale."""

    scale_id: str
    profiles: dict[str, UserProfile] = field(default_factory=dict)
    max_seen_measurements: int = DEFAULT_DEDUPLICATION_CACHE_SIZE
    last_measurement: Measurement | None = None
    last_processed_measurement: ProcessedMeasurement | None = None
    latest_by_profile: dict[str, ProcessedMeasurement] = field(
        default_factory=dict, init=False
    )
    _seen: OrderedDict[str, None] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _listeners: set[MeasurementListener] = field(
        default_factory=set, init=False, repr=False
    )
    _published_measurement_id: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_seen_measurements < 1:
            raise ValueError("max_seen_measurements must be positive")
        self.profiles = dict(self.profiles)
        for profile_pin, profile in self.profiles.items():
            if not isinstance(profile, UserProfile):
                raise TypeError("profiles must contain UserProfile values")
            if profile_pin != profile.profile_pin:
                raise ValueError("profile mapping key must match profile_pin")

    @property
    def seen_count(self) -> int:
        """Return the number of IDs currently held by the bounded cache."""
        return len(self._seen)

    @property
    def seen_measurement_ids(self) -> tuple[str, ...]:
        """Return retained IDs from oldest to newest for persistence."""
        return tuple(self._seen)

    @property
    def listener_count(self) -> int:
        """Return the number of active push consumers."""
        return len(self._listeners)

    @property
    def last_profile_pin(self) -> str | None:
        """Return the profile PIN of the last accepted measurement."""
        if self.last_processed_measurement is None:
            return None
        return self.last_processed_measurement.measurement.profile_pin

    def restore_state(
        self,
        *,
        seen_measurement_ids: Iterable[str],
        latest_by_profile: Mapping[str, ProcessedMeasurement],
        last_profile_pin: str | None,
    ) -> None:
        """Atomically restore a validated persistent runtime snapshot."""
        restored_seen: OrderedDict[str, None] = OrderedDict()
        for measurement_id in seen_measurement_ids:
            if (
                not isinstance(measurement_id, str)
                or not measurement_id
                or measurement_id != measurement_id.strip()
                or len(measurement_id) > 128
            ):
                raise ValueError("stored measurement IDs are invalid")
            if measurement_id in restored_seen:
                raise ValueError("stored measurement IDs must be unique")
            restored_seen[measurement_id] = None

        while len(restored_seen) > self.max_seen_measurements:
            restored_seen.popitem(last=False)

        restored_latest = dict(latest_by_profile)
        restored_profile_ids: set[str] = set()
        for profile_pin, processed in restored_latest.items():
            if not isinstance(processed, ProcessedMeasurement):
                raise TypeError(
                    "latest_by_profile must contain ProcessedMeasurement values"
                )
            if profile_pin != processed.measurement.profile_pin:
                raise ValueError("stored profile mapping key must match profile_pin")
            if processed.measurement.scale_id != self.scale_id:
                raise ValueError("stored measurement belongs to a different scale")
            profile_id = processed.user_profile.profile_id
            if profile_id in restored_profile_ids:
                raise ValueError("stored profile snapshots must have unique IDs")
            restored_profile_ids.add(profile_id)

        if last_profile_pin is None:
            if restored_latest or restored_seen:
                raise ValueError("stored state has no last profile")
            last_processed = None
        else:
            try:
                last_processed = restored_latest[last_profile_pin]
            except KeyError as err:
                raise ValueError("stored last profile is missing") from err
            if last_processed.measurement.measurement_id not in restored_seen:
                raise ValueError("stored last measurement is absent from deduplication")

        # All validation happens above; these assignments form one restore commit.
        self._seen = restored_seen
        self.latest_by_profile = restored_latest
        self.last_processed_measurement = last_processed
        self.last_measurement = (
            None if last_processed is None else last_processed.measurement
        )
        self._published_measurement_id = (
            None
            if last_processed is None
            else last_processed.measurement.measurement_id
        )

    def latest_for_profile(self, profile: UserProfile) -> ProcessedMeasurement | None:
        """Return the latest snapshot, surviving a configured PIN change."""
        if (
            latest := self.latest_by_profile.get(profile.profile_pin)
        ) and latest.user_profile.profile_id == profile.profile_id:
            return latest
        return next(
            (
                processed
                for processed in self.latest_by_profile.values()
                if processed.user_profile.profile_id == profile.profile_id
            ),
            None,
        )

    def add_listener(self, listener: MeasurementListener) -> Callable[[], None]:
        """Subscribe a push consumer and return an idempotent remover."""
        self._listeners.add(listener)

        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    def checkpoint(self) -> RuntimeCheckpoint:
        """Capture mutable runtime state before an atomic Store write."""
        return RuntimeCheckpoint(
            seen_measurement_ids=tuple(self._seen),
            latest_by_profile=dict(self.latest_by_profile),
            last_measurement=self.last_measurement,
            last_processed_measurement=self.last_processed_measurement,
            published_measurement_id=self._published_measurement_id,
        )

    def restore_checkpoint(self, checkpoint: RuntimeCheckpoint) -> None:
        """Roll back an acceptance whose persistent Store write failed."""
        if not isinstance(checkpoint, RuntimeCheckpoint):
            raise TypeError("checkpoint must be a RuntimeCheckpoint")
        self._seen = OrderedDict.fromkeys(checkpoint.seen_measurement_ids)
        self.latest_by_profile = dict(checkpoint.latest_by_profile)
        self.last_measurement = checkpoint.last_measurement
        self.last_processed_measurement = checkpoint.last_processed_measurement
        self._published_measurement_id = checkpoint.published_measurement_id

    def publish_last_processed(self) -> None:
        """Publish the latest persisted sample exactly once to active consumers."""
        processed = self.last_processed_measurement
        if processed is None:
            return
        measurement_id = processed.measurement.measurement_id
        if measurement_id == self._published_measurement_id:
            return

        for listener in tuple(self._listeners):
            try:
                listener(processed)
            except Exception:
                _LOGGER.exception("Error publishing GARLYN measurement update")
        self._published_measurement_id = measurement_id

    def process(self, measurement: Measurement) -> AcceptanceStatus:
        """Resolve, calculate, and atomically accept a measurement."""
        if measurement.scale_id != self.scale_id:
            raise ValueError("measurement belongs to a different scale")

        measurement_id = measurement.measurement_id
        if measurement_id in self._seen:
            self._seen.move_to_end(measurement_id)
            return AcceptanceStatus.DUPLICATE

        try:
            user_profile = self.profiles[measurement.profile_pin]
        except KeyError as err:
            raise UnknownProfileError(measurement.profile_pin) from err

        try:
            algorithm_profile = user_profile.algorithm_profile(measurement.measured_at)
        except (TypeError, ValueError) as err:
            raise InvalidProfileForMeasurementError(measurement.profile_pin) from err

        calculated = ProcessedMeasurement(
            measurement=measurement,
            user_profile=user_profile,
            algorithm_profile=algorithm_profile,
            result=calculate_body_composition(
                measurement.algorithm_input(algorithm_profile)
            ),
        )

        # No awaits occur between these assignments. In Home Assistant's event
        # loop this is the atomic commit point for a fully calculated sample.
        self._seen[measurement_id] = None
        while len(self._seen) > self.max_seen_measurements:
            self._seen.popitem(last=False)
        self.last_measurement = measurement
        self.last_processed_measurement = calculated
        for profile_pin, existing in tuple(self.latest_by_profile.items()):
            if (
                profile_pin != measurement.profile_pin
                and existing.user_profile.profile_id == user_profile.profile_id
            ):
                del self.latest_by_profile[profile_pin]
        self.latest_by_profile[measurement.profile_pin] = calculated
        return AcceptanceStatus.ACCEPTED
