"""Tests for the pure native-compatible profile models."""

import pytest

from custom_components.garlyn_scale.algorithm import (
    ActivityLevel,
    AlgorithmProfile,
    ReferenceStandard,
    Sex,
    amr_factor,
)


def test_current_athlete_mode_mapping() -> None:
    assert ActivityLevel.from_athlete_mode(False) is ActivityLevel.STANDARD
    assert ActivityLevel.from_athlete_mode(True) is ActivityLevel.EXTREMELY_ACTIVE


@pytest.mark.parametrize(
    ("level", "sex", "expected"),
    [
        (ActivityLevel.STANDARD, Sex.MALE, 1.54),
        (ActivityLevel.STANDARD, Sex.FEMALE, 1.32),
        (ActivityLevel.SEDENTARY, Sex.MALE, 1.2),
        (ActivityLevel.LIGHT, Sex.FEMALE, 1.375),
        (ActivityLevel.MODERATE, Sex.MALE, 1.55),
        (ActivityLevel.VERY_ACTIVE, Sex.FEMALE, 1.725),
        (ActivityLevel.EXTREMELY_ACTIVE, Sex.MALE, 1.9),
    ],
)
def test_native_amr_factors(level: ActivityLevel, sex: Sex, expected: float) -> None:
    assert amr_factor(level, sex) == pytest.approx(expected)


def test_algorithm_profile_normalizes_native_values() -> None:
    profile = AlgorithmProfile(
        sex=1,
        age_years=34,
        height_cm=175,
        activity_level=0,
        reference_standard="external",
    )
    assert profile.sex is Sex.MALE
    assert profile.activity_level is ActivityLevel.STANDARD
    assert profile.reference_standard is ReferenceStandard.EXTERNAL


def test_activity_outside_native_range_is_rejected() -> None:
    with pytest.raises(ValueError):
        AlgorithmProfile(
            sex=Sex.MALE,
            age_years=34,
            height_cm=175,
            activity_level=6,
        )
