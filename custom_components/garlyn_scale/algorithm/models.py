"""Pure input and output models for the body-composition algorithm."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Sex(IntEnum):
    """Native sex values used by MovingLife."""

    FEMALE = 0
    MALE = 1


class ActivityLevel(IntEnum):
    """Native bhActivityLevel values accepted by bodyFatScaleAlg_2.2.2."""

    STANDARD = 0
    SEDENTARY = 1
    LIGHT = 2
    MODERATE = 3
    VERY_ACTIVE = 4
    EXTREMELY_ACTIVE = 5

    @classmethod
    def from_athlete_mode(cls, enabled: bool) -> ActivityLevel:
        """Map the current MovingLife Athlete Mode switch to its native value."""
        if not isinstance(enabled, bool):
            raise TypeError("athlete mode must be a boolean")
        return cls.EXTREMELY_ACTIVE if enabled else cls.STANDARD


class ReferenceStandard(StrEnum):
    """Binary native reference-standard selector (bhNationality)."""

    EXTERNAL = "external"
    INTERNAL = "internal"


_ACTIVITY_FACTORS: dict[ActivityLevel, float] = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.VERY_ACTIVE: 1.725,
    ActivityLevel.EXTREMELY_ACTIVE: 1.9,
}


def amr_factor(activity_level: ActivityLevel, sex: Sex) -> float:
    """Return the native AMR multiplier recovered from the ARM64 library."""
    activity_level = ActivityLevel(activity_level)
    sex = Sex(sex)
    if activity_level is ActivityLevel.STANDARD:
        return 1.54 if sex is Sex.MALE else 1.32
    return _ACTIVITY_FACTORS[activity_level]


def _finite_positive(value: object, field_name: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be a number")
    converted = float(value)
    if not math.isfinite(converted) or not 0 < converted <= maximum:
        raise ValueError(f"{field_name} must be finite and in (0, {maximum}]")
    return converted


@dataclass(frozen=True, slots=True)
class SegmentalImpedance:
    """Five impedance values in the confirmed GARLYN segment order."""

    left_arm: float
    right_arm: float
    left_leg: float
    right_leg: float
    trunk: float

    def __post_init__(self) -> None:
        for field_name in (
            "left_arm",
            "right_arm",
            "left_leg",
            "right_leg",
            "trunk",
        ):
            value = _finite_positive(getattr(self, field_name), field_name, 10_000)
            object.__setattr__(self, field_name, value)

    @classmethod
    def from_sequence(cls, values: Sequence[object]) -> SegmentalImpedance:
        """Create a segment vector from left arm through trunk."""
        if isinstance(values, str | bytes) or len(values) != 5:
            raise ValueError("segmental impedance must contain exactly five values")
        return cls(*values)

    def as_tuple(self) -> tuple[float, float, float, float, float]:
        """Return values in the transport/native order."""
        return (
            self.left_arm,
            self.right_arm,
            self.left_leg,
            self.right_leg,
            self.trunk,
        )


@dataclass(frozen=True, slots=True)
class AlgorithmProfile:
    """Profile values passed to the native-compatible algorithm boundary."""

    sex: Sex
    age_years: int
    height_cm: float
    activity_level: ActivityLevel = ActivityLevel.STANDARD
    reference_standard: ReferenceStandard = ReferenceStandard.EXTERNAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "sex", Sex(self.sex))
        object.__setattr__(self, "activity_level", ActivityLevel(self.activity_level))
        object.__setattr__(
            self, "reference_standard", ReferenceStandard(self.reference_standard)
        )
        if isinstance(self.age_years, bool) or not isinstance(self.age_years, int):
            raise TypeError("age_years must be an integer")
        if not 1 <= self.age_years <= 120:
            raise ValueError("age_years must be in [1, 120]")
        object.__setattr__(
            self,
            "height_cm",
            _finite_positive(self.height_cm, "height_cm", 300),
        )


@dataclass(frozen=True, slots=True)
class AlgorithmInput:
    """Complete pure input for one dual-frequency calculation."""

    profile: AlgorithmProfile
    weight_kg: float
    bia_20khz: SegmentalImpedance
    bia_100khz: SegmentalImpedance

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "weight_kg",
            _finite_positive(self.weight_kg, "weight_kg", 500),
        )


@dataclass(frozen=True, slots=True)
class BodyCompositionResult:
    """Outputs already verified against MovingLife runtime data."""

    body_fat_pct: float
    body_fat_kg: float
    muscle_pct: float
    muscle_kg: float
    body_water_pct: float
    body_water_kg: float
    bmr_kcal: int
    bmi: float
