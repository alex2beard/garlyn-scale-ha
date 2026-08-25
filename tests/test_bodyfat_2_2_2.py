"""Regression tests for the MovingLife-compatible eight-electrode port."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.garlyn_scale.algorithm import (
    ActivityLevel,
    AlgorithmInput,
    AlgorithmProfile,
    ReferenceStandard,
    SegmentalImpedance,
    Sex,
    calculate_body_composition,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "synthetic_reference.json"


def _synthetic_input() -> tuple[AlgorithmInput, dict[str, float | int]]:
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    profile = fixture["profile"]
    measurement = fixture["measurement"]
    return (
        AlgorithmInput(
            profile=AlgorithmProfile(
                sex=Sex.MALE if profile["sex"] == "male" else Sex.FEMALE,
                age_years=profile["age_years"],
                height_cm=profile["height_cm"],
                activity_level=ActivityLevel(profile["activity_level"]),
                reference_standard=ReferenceStandard(profile["reference_standard"]),
            ),
            weight_kg=measurement["weight_kg"],
            bia_20khz=SegmentalImpedance.from_sequence(
                measurement["bia"]["20khz"]
            ),
            bia_100khz=SegmentalImpedance.from_sequence(
                measurement["bia"]["100khz"]
            ),
        ),
        fixture["expected"],
    )


def test_synthetic_vector_matches_binary32_regression() -> None:
    data, _ = _synthetic_input()

    result = calculate_body_composition(data)

    assert result.body_fat_pct == 32.80478286743164
    assert result.body_fat_kg == 24.537979125976562
    assert result.muscle_pct == 62.367652893066406
    assert result.muscle_kg == 46.65100860595703
    assert result.body_water_pct == 49.12552261352539
    assert result.body_water_kg == 36.74589157104492
    assert result.bmr_kcal == 1455
    assert result.bmi == 24.424489974975586


def test_synthetic_vector_matches_fixture_precision() -> None:
    data, expected = _synthetic_input()

    result = calculate_body_composition(data)

    for field_name, expected_value in expected.items():
        actual = getattr(result, field_name)
        if isinstance(expected_value, int):
            assert actual == expected_value
        else:
            assert actual == pytest.approx(expected_value, abs=5e-6)


def test_unverified_profile_selectors_do_not_change_verified_outputs() -> None:
    data, _ = _synthetic_input()
    alternate_profile = AlgorithmProfile(
        sex=Sex.FEMALE,
        age_years=72,
        height_cm=data.profile.height_cm,
        activity_level=ActivityLevel.EXTREMELY_ACTIVE,
        reference_standard=ReferenceStandard.INTERNAL,
    )
    alternate = AlgorithmInput(
        profile=alternate_profile,
        weight_kg=data.weight_kg,
        bia_20khz=data.bia_20khz,
        bia_100khz=data.bia_100khz,
    )

    assert calculate_body_composition(alternate) == calculate_body_composition(data)


def test_calculation_requires_typed_input() -> None:
    with pytest.raises(TypeError, match="AlgorithmInput"):
        calculate_body_composition(object())  # type: ignore[arg-type]
