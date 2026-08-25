"""Strict ESP-to-Home-Assistant transport contract."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .algorithm.models import AlgorithmInput, AlgorithmProfile, SegmentalImpedance
from .const import TRANSPORT_PROTOCOL_VERSION

_ROOT_KEYS = frozenset(
    {
        "protocol_version",
        "scale_id",
        "measurement_id",
        "measured_at",
        "profile_pin",
        "weight_kg",
        "bia",
    }
)
_BIA_KEYS = frozenset({"20khz", "100khz"})


class TransportValidationError(ValueError):
    """Raised when a webhook payload violates the transport contract."""


@dataclass(frozen=True, slots=True)
class Measurement:
    """Validated version-1 measurement from an ESP transport."""

    protocol_version: int
    scale_id: str
    measurement_id: str
    measured_at: datetime
    profile_pin: str
    weight_kg: float
    bia_20khz: SegmentalImpedance
    bia_100khz: SegmentalImpedance

    def algorithm_input(self, profile: AlgorithmProfile) -> AlgorithmInput:
        """Combine transport data with a resolved user profile."""
        return AlgorithmInput(
            profile=profile,
            weight_kg=self.weight_kg,
            bia_20khz=self.bia_20khz,
            bia_100khz=self.bia_100khz,
        )


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], path: str) -> None:
    keys = frozenset(value)
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"unknown {extra}")
        raise TransportValidationError(f"{path}: {', '.join(details)}")


def _bounded_string(value: object, path: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TransportValidationError(f"{path} must be a string")
    if not value or value != value.strip() or len(value) > maximum:
        raise TransportValidationError(
            f"{path} must be non-empty, trimmed, and at most {maximum} characters"
        )
    return value


def _positive_float(value: object, path: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TransportValidationError(f"{path} must be a number")
    converted = float(value)
    if not math.isfinite(converted) or not 0 < converted <= maximum:
        raise TransportValidationError(f"{path} must be finite and in (0, {maximum}]")
    return converted


def _timestamp(value: object) -> datetime:
    raw = _bounded_string(value, "measured_at", 64)
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as err:
        raise TransportValidationError("measured_at must be ISO 8601") from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TransportValidationError("measured_at must include a UTC offset")
    return parsed


def _segments(value: object, path: str) -> SegmentalImpedance:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TransportValidationError(f"{path} must be an array")
    if len(value) != 5:
        raise TransportValidationError(f"{path} must contain exactly five values")
    converted = [
        _positive_float(item, f"{path}[{index}]", 10_000)
        for index, item in enumerate(value)
    ]
    return SegmentalImpedance.from_sequence(converted)


def parse_measurement(
    payload: object, *, expected_scale_id: str | None = None
) -> Measurement:
    """Validate and parse a transport-v1 measurement payload."""
    if not isinstance(payload, Mapping):
        raise TransportValidationError("payload must be a JSON object")
    _exact_keys(payload, _ROOT_KEYS, "payload")

    protocol_version = payload["protocol_version"]
    if type(protocol_version) is not int:  # bool is not a valid JSON protocol integer
        raise TransportValidationError("protocol_version must be an integer")
    if protocol_version != TRANSPORT_PROTOCOL_VERSION:
        raise TransportValidationError(
            f"unsupported protocol_version {protocol_version}"
        )

    scale_id = _bounded_string(payload["scale_id"], "scale_id", 128)
    if expected_scale_id is not None and scale_id != expected_scale_id:
        raise TransportValidationError("scale_id does not match this webhook")

    measurement_id = _bounded_string(payload["measurement_id"], "measurement_id", 128)
    profile_pin = _bounded_string(payload["profile_pin"], "profile_pin", 4)
    if len(profile_pin) != 4 or not profile_pin.isdigit():
        raise TransportValidationError(
            "profile_pin must contain exactly four decimal digits"
        )

    bia = payload["bia"]
    if not isinstance(bia, Mapping):
        raise TransportValidationError("bia must be a JSON object")
    _exact_keys(bia, _BIA_KEYS, "bia")

    return Measurement(
        protocol_version=protocol_version,
        scale_id=scale_id,
        measurement_id=measurement_id,
        measured_at=_timestamp(payload["measured_at"]),
        profile_pin=profile_pin,
        weight_kg=_positive_float(payload["weight_kg"], "weight_kg", 500),
        bia_20khz=_segments(bia["20khz"], "bia.20khz"),
        bia_100khz=_segments(bia["100khz"], "bia.100khz"),
    )
