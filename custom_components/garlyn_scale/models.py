"""Configuration-domain models for GARLYN Scale."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .algorithm.models import (
    ActivityLevel,
    AlgorithmProfile,
    ReferenceStandard,
    Sex,
)
from .const import (
    CONF_ATHLETE_MODE,
    CONF_DATE_OF_BIRTH,
    CONF_HEIGHT_CM,
    CONF_PROFILE_ID,
    CONF_PROFILE_NAME,
    CONF_PROFILES,
    CONF_REFERENCE_STANDARD,
    CONF_SEX,
    CONF_SPARKY_API_KEY,
    CONF_SPARKY_ENABLED,
    DOMAIN,
)

_LEGACY_STORED_PROFILE_KEYS = frozenset(
    {
        CONF_PROFILE_NAME,
        CONF_SEX,
        CONF_DATE_OF_BIRTH,
        CONF_HEIGHT_CM,
        CONF_ATHLETE_MODE,
        CONF_REFERENCE_STANDARD,
    }
)
_STORED_PROFILE_KEYS = _LEGACY_STORED_PROFILE_KEYS | {CONF_PROFILE_ID}
_SPARKY_STORED_PROFILE_KEYS = _STORED_PROFILE_KEYS | {
    CONF_SPARKY_ENABLED,
    CONF_SPARKY_API_KEY,
}


def _legacy_profile_id(profile_pin: str) -> str:
    """Derive a stable ID for a profile stored before IDs were introduced."""
    return uuid5(NAMESPACE_URL, f"{DOMAIN}:profile:{profile_pin}").hex


def age_on(birth_date: date, measured_date: date) -> int:
    """Calculate completed years on the measurement date."""
    if measured_date < birth_date:
        raise ValueError("measurement date cannot be before date of birth")
    return (
        measured_date.year
        - birth_date.year
        - (
            (measured_date.month, measured_date.day)
            < (birth_date.month, birth_date.day)
        )
    )


@dataclass(frozen=True, slots=True)
class UserProfile:
    """Persistable profile settings; secrets are excluded from repr."""

    name: str
    profile_pin: str
    sex: Sex
    date_of_birth: date
    height_cm: float
    profile_id: str | None = None
    athlete_mode: bool = False
    reference_standard: ReferenceStandard = ReferenceStandard.EXTERNAL
    person_entity_id: str | None = None
    sparky_enabled: bool = False
    sparky_api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("name must be a string")
        if not self.name or self.name != self.name.strip() or len(self.name) > 64:
            raise ValueError(
                "name must be non-empty, trimmed, and at most 64 characters"
            )
        if not isinstance(self.profile_pin, str):
            raise TypeError("profile_pin must be a string")
        if len(self.profile_pin) != 4 or not self.profile_pin.isdigit():
            raise ValueError("profile_pin must contain exactly four decimal digits")
        profile_id = self.profile_id
        if profile_id is None:
            profile_id = _legacy_profile_id(self.profile_pin)
        if (
            not isinstance(profile_id, str)
            or len(profile_id) != 32
            or any(character not in "0123456789abcdef" for character in profile_id)
        ):
            raise ValueError("profile_id must contain 32 lowercase hexadecimal digits")
        object.__setattr__(self, "profile_id", profile_id)
        if not isinstance(self.date_of_birth, date) or isinstance(
            self.date_of_birth, datetime
        ):
            raise TypeError("date_of_birth must be a date")
        object.__setattr__(self, "sex", Sex(self.sex))
        object.__setattr__(
            self, "reference_standard", ReferenceStandard(self.reference_standard)
        )
        if not isinstance(self.athlete_mode, bool):
            raise TypeError("athlete_mode must be a boolean")
        if not isinstance(self.sparky_enabled, bool):
            raise TypeError("sparky_enabled must be a boolean")
        if self.sparky_api_key is not None:
            if not isinstance(self.sparky_api_key, str):
                raise TypeError("sparky_api_key must be a string or null")
            if (
                not self.sparky_api_key
                or self.sparky_api_key != self.sparky_api_key.strip()
                or len(self.sparky_api_key) > 512
                or not self.sparky_api_key.isascii()
                or any(
                    not 33 <= ord(character) <= 126 for character in self.sparky_api_key
                )
            ):
                raise ValueError(
                    "sparky_api_key must be a non-empty ASCII token without "
                    "whitespace or control characters and at most 512 characters"
                )
        if self.sparky_enabled and self.sparky_api_key is None:
            raise ValueError("sparky_api_key is required when Sparky sync is enabled")
        if isinstance(self.height_cm, bool) or not isinstance(
            self.height_cm, int | float
        ):
            raise TypeError("height_cm must be a number")
        height = float(self.height_cm)
        if not math.isfinite(height) or not 0 < height <= 300:
            raise ValueError("height_cm must be finite and in (0, 300]")
        object.__setattr__(self, "height_cm", height)

    def algorithm_profile(self, measured_at: datetime) -> AlgorithmProfile:
        """Derive the native-compatible profile for a specific measurement."""
        if measured_at.tzinfo is None or measured_at.utcoffset() is None:
            raise ValueError("measured_at must include a UTC offset")
        return AlgorithmProfile(
            sex=self.sex,
            age_years=age_on(self.date_of_birth, measured_at.date()),
            height_cm=self.height_cm,
            activity_level=ActivityLevel.from_athlete_mode(self.athlete_mode),
            reference_standard=self.reference_standard,
        )


def serialize_profiles(
    profiles: Mapping[str, UserProfile],
    *,
    include_sparky: bool = True,
) -> dict[str, dict[str, object]]:
    """Serialize profiles to deterministic JSON-safe config-entry options."""
    serialized: dict[str, dict[str, object]] = {}
    for profile_pin in sorted(profiles):
        profile = profiles[profile_pin]
        if not isinstance(profile, UserProfile):
            raise TypeError("profiles must contain UserProfile values")
        if profile_pin != profile.profile_pin:
            raise ValueError("profile mapping key must match profile_pin")
        stored: dict[str, object] = {
            CONF_PROFILE_ID: profile.profile_id,
            CONF_PROFILE_NAME: profile.name,
            CONF_SEX: profile.sex.name.lower(),
            CONF_DATE_OF_BIRTH: profile.date_of_birth.isoformat(),
            CONF_HEIGHT_CM: profile.height_cm,
            CONF_ATHLETE_MODE: profile.athlete_mode,
            CONF_REFERENCE_STANDARD: profile.reference_standard.value,
        }
        if include_sparky:
            stored[CONF_SPARKY_ENABLED] = profile.sparky_enabled
            stored[CONF_SPARKY_API_KEY] = profile.sparky_api_key
        serialized[profile_pin] = stored
    return serialized


def deserialize_profiles(options: Mapping[str, Any]) -> dict[str, UserProfile]:
    """Load and strictly validate profiles from config-entry options."""
    raw_profiles = options.get(CONF_PROFILES, {})
    if not isinstance(raw_profiles, Mapping):
        raise ValueError("profiles option must be a mapping")

    profiles: dict[str, UserProfile] = {}
    for profile_pin, raw_profile in raw_profiles.items():
        if not isinstance(profile_pin, str):
            raise ValueError("profile PIN keys must be strings")
        if not isinstance(raw_profile, Mapping):
            raise ValueError(f"profile {profile_pin!r} must be a mapping")
        stored_keys = frozenset(raw_profile)
        if stored_keys not in (
            _LEGACY_STORED_PROFILE_KEYS,
            _STORED_PROFILE_KEYS,
            _SPARKY_STORED_PROFILE_KEYS,
        ):
            raise ValueError(f"profile {profile_pin!r} has invalid stored fields")

        raw_birth_date = raw_profile[CONF_DATE_OF_BIRTH]
        if not isinstance(raw_birth_date, str):
            raise ValueError(f"profile {profile_pin!r} birth date must be a string")
        try:
            birth_date = date.fromisoformat(raw_birth_date)
        except ValueError as err:
            raise ValueError(
                f"profile {profile_pin!r} birth date must be ISO 8601"
            ) from err

        raw_sex = raw_profile[CONF_SEX]
        if not isinstance(raw_sex, str):
            raise ValueError(f"profile {profile_pin!r} sex must be a string")
        try:
            sex = Sex[raw_sex.upper()]
        except KeyError as err:
            raise ValueError(f"profile {profile_pin!r} has invalid sex") from err

        profiles[profile_pin] = UserProfile(
            name=raw_profile[CONF_PROFILE_NAME],
            profile_pin=profile_pin,
            profile_id=raw_profile.get(CONF_PROFILE_ID),
            sex=sex,
            date_of_birth=birth_date,
            height_cm=raw_profile[CONF_HEIGHT_CM],
            athlete_mode=raw_profile[CONF_ATHLETE_MODE],
            reference_standard=raw_profile[CONF_REFERENCE_STANDARD],
            sparky_enabled=raw_profile.get(CONF_SPARKY_ENABLED, False),
            sparky_api_key=raw_profile.get(CONF_SPARKY_API_KEY),
        )

    profile_ids = [profile.profile_id for profile in profiles.values()]
    if len(profile_ids) != len(set(profile_ids)):
        raise ValueError("profile IDs must be unique")
    return profiles
