"""Restart-safe persistent state for one GARLYN scale config entry."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .algorithm import BodyCompositionResult
from .const import CONF_PROFILES, DOMAIN, TRANSPORT_PROTOCOL_VERSION
from .models import deserialize_profiles, serialize_profiles
from .runtime import ProcessedMeasurement, ScaleRuntime
from .sparky import SparkyOutbox
from .transport import parse_measurement

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store

_STORAGE_VERSION = 1
_STORAGE_MINOR_VERSION = 2
_STORAGE_KEY_PREFIX = f"{DOMAIN}.runtime"
_MAX_STORED_MEASUREMENT_IDS = 10_000

_LEGACY_STATE_KEYS = frozenset(
    {
        "scale_id",
        "seen_measurement_ids",
        "latest_by_profile",
        "last_profile_pin",
    }
)
_STATE_KEYS = _LEGACY_STATE_KEYS | {"sparky_outbox"}
_PROCESSED_KEYS = frozenset({"measurement", "profile", "result", "algorithm_version"})
_RESULT_KEYS = frozenset(
    {
        "body_fat_pct",
        "body_fat_kg",
        "muscle_pct",
        "muscle_kg",
        "body_water_pct",
        "body_water_kg",
        "bmr_kcal",
        "bmi",
    }
)


def _exact_mapping(value: object, expected: frozenset[str], path: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping")
    if frozenset(value) != expected:
        raise ValueError(f"{path} has invalid stored fields")
    return value


def _finite_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{path} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{path} must be finite")
    return converted


def _serialize_measurement(processed: ProcessedMeasurement) -> dict[str, object]:
    measurement = processed.measurement
    return {
        "protocol_version": measurement.protocol_version,
        "scale_id": measurement.scale_id,
        "measurement_id": measurement.measurement_id,
        "measured_at": measurement.measured_at.isoformat(),
        "profile_pin": measurement.profile_pin,
        "weight_kg": measurement.weight_kg,
        "bia": {
            "20khz": list(measurement.bia_20khz.as_tuple()),
            "100khz": list(measurement.bia_100khz.as_tuple()),
        },
    }


def _serialize_result(result: BodyCompositionResult) -> dict[str, int | float]:
    return {
        "body_fat_pct": result.body_fat_pct,
        "body_fat_kg": result.body_fat_kg,
        "muscle_pct": result.muscle_pct,
        "muscle_kg": result.muscle_kg,
        "body_water_pct": result.body_water_pct,
        "body_water_kg": result.body_water_kg,
        "bmr_kcal": result.bmr_kcal,
        "bmi": result.bmi,
    }


def _serialize_processed(processed: ProcessedMeasurement) -> dict[str, object]:
    profile_pin = processed.measurement.profile_pin
    return {
        "measurement": _serialize_measurement(processed),
        "profile": serialize_profiles(
            {profile_pin: processed.user_profile}, include_sparky=False
        )[profile_pin],
        "result": _serialize_result(processed.result),
        "algorithm_version": processed.algorithm_version,
    }


def serialize_runtime_state(
    runtime: ScaleRuntime, outbox: SparkyOutbox | None = None
) -> dict[str, object]:
    """Return a deterministic JSON-safe snapshot of accepted runtime state."""
    return {
        "scale_id": runtime.scale_id,
        "seen_measurement_ids": list(runtime.seen_measurement_ids),
        "latest_by_profile": {
            profile_pin: _serialize_processed(runtime.latest_by_profile[profile_pin])
            for profile_pin in sorted(runtime.latest_by_profile)
        },
        "last_profile_pin": runtime.last_profile_pin,
        "sparky_outbox": (outbox or SparkyOutbox()).as_dict(),
    }


def _deserialize_result(value: object) -> BodyCompositionResult:
    stored = _exact_mapping(value, _RESULT_KEYS, "result")
    bmr_kcal = stored["bmr_kcal"]
    if type(bmr_kcal) is not int:
        raise ValueError("result.bmr_kcal must be an integer")
    return BodyCompositionResult(
        body_fat_pct=_finite_number(stored["body_fat_pct"], "result.body_fat_pct"),
        body_fat_kg=_finite_number(stored["body_fat_kg"], "result.body_fat_kg"),
        muscle_pct=_finite_number(stored["muscle_pct"], "result.muscle_pct"),
        muscle_kg=_finite_number(stored["muscle_kg"], "result.muscle_kg"),
        body_water_pct=_finite_number(
            stored["body_water_pct"], "result.body_water_pct"
        ),
        body_water_kg=_finite_number(stored["body_water_kg"], "result.body_water_kg"),
        bmr_kcal=bmr_kcal,
        bmi=_finite_number(stored["bmi"], "result.bmi"),
    )


def _deserialize_processed(
    value: object, *, profile_pin: str, scale_id: str
) -> ProcessedMeasurement:
    stored = _exact_mapping(value, _PROCESSED_KEYS, f"profile {profile_pin}")
    measurement = parse_measurement(stored["measurement"], expected_scale_id=scale_id)
    if measurement.protocol_version != TRANSPORT_PROTOCOL_VERSION:
        raise ValueError("stored measurement uses an unsupported protocol")
    if measurement.profile_pin != profile_pin:
        raise ValueError("stored measurement profile PIN does not match its key")

    profiles = deserialize_profiles({CONF_PROFILES: {profile_pin: stored["profile"]}})
    user_profile = profiles[profile_pin]
    algorithm_profile = user_profile.algorithm_profile(measurement.measured_at)

    algorithm_version = stored["algorithm_version"]
    if (
        not isinstance(algorithm_version, str)
        or not algorithm_version
        or algorithm_version != algorithm_version.strip()
        or len(algorithm_version) > 64
    ):
        raise ValueError("stored algorithm version is invalid")

    return ProcessedMeasurement(
        measurement=measurement,
        user_profile=user_profile,
        algorithm_profile=algorithm_profile,
        result=_deserialize_result(stored["result"]),
        algorithm_version=algorithm_version,
    )


def restore_runtime_state(runtime: ScaleRuntime, value: object) -> SparkyOutbox:
    """Validate and restore runtime state, returning its optional Sparky outbox."""
    if not isinstance(value, Mapping):
        raise ValueError("runtime state must be a mapping")
    stored_keys = frozenset(value)
    if stored_keys not in (_LEGACY_STATE_KEYS, _STATE_KEYS):
        raise ValueError("runtime state has invalid stored fields")
    stored = value
    scale_id = stored["scale_id"]
    if scale_id != runtime.scale_id:
        raise ValueError("stored runtime state belongs to a different scale")

    raw_seen = stored["seen_measurement_ids"]
    if not isinstance(raw_seen, list):
        raise ValueError("seen_measurement_ids must be a list")
    if len(raw_seen) > _MAX_STORED_MEASUREMENT_IDS:
        raise ValueError("too many stored measurement IDs")

    raw_latest = stored["latest_by_profile"]
    if not isinstance(raw_latest, Mapping):
        raise ValueError("latest_by_profile must be a mapping")
    latest_by_profile: dict[str, ProcessedMeasurement] = {}
    for profile_pin, processed in raw_latest.items():
        if (
            not isinstance(profile_pin, str)
            or len(profile_pin) != 4
            or not profile_pin.isdigit()
        ):
            raise ValueError("stored profile PIN is invalid")
        latest_by_profile[profile_pin] = _deserialize_processed(
            processed,
            profile_pin=profile_pin,
            scale_id=runtime.scale_id,
        )

    last_profile_pin = stored["last_profile_pin"]
    if last_profile_pin is not None and not isinstance(last_profile_pin, str):
        raise ValueError("last_profile_pin must be a string or null")

    outbox = SparkyOutbox.from_dict(stored.get("sparky_outbox", []))

    runtime.restore_state(
        seen_measurement_ids=raw_seen,
        latest_by_profile=latest_by_profile,
        last_profile_pin=last_profile_pin,
    )
    return outbox


class RuntimeStateStore:
    """Home Assistant Store wrapper for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry_id}",
            private=True,
            atomic_writes=True,
            minor_version=_STORAGE_MINOR_VERSION,
        )

    async def async_load(self, runtime: ScaleRuntime) -> SparkyOutbox:
        """Load state if it exists and return the restored Sparky outbox."""
        if (stored := await self._store.async_load()) is not None:
            return restore_runtime_state(runtime, stored)
        return SparkyOutbox()

    async def async_save(
        self, runtime: ScaleRuntime, outbox: SparkyOutbox | None = None
    ) -> None:
        """Persist the current accepted state before acknowledging delivery."""
        await self._store.async_save(serialize_runtime_state(runtime, outbox))

    async def async_remove(self) -> None:
        """Remove persistent state with its config entry."""
        await self._store.async_remove()
