"""Tests for configured user profiles."""

from datetime import date, datetime

import pytest

from custom_components.garlyn_scale.algorithm import ActivityLevel, Sex
from custom_components.garlyn_scale.const import CONF_PROFILE_ID, CONF_PROFILES
from custom_components.garlyn_scale.models import (
    UserProfile,
    age_on,
    deserialize_profiles,
    serialize_profiles,
)


def test_age_on_birthday_boundary() -> None:
    birth = date(1990, 4, 10)
    assert age_on(birth, date(2026, 4, 9)) == 35
    assert age_on(birth, date(2026, 4, 10)) == 36


def test_user_profile_derives_standard_mode_when_switch_is_off() -> None:
    profile = UserProfile(
        name="Test user",
        profile_pin="0123",
        sex=Sex.MALE,
        date_of_birth=date(1991, 6, 15),
        height_cm=175,
        athlete_mode=False,
    )
    algorithm_profile = profile.algorithm_profile(
        datetime.fromisoformat("2026-01-15T12:00:00+00:00")
    )
    assert algorithm_profile.age_years == 34
    assert algorithm_profile.activity_level is ActivityLevel.STANDARD


def test_profile_pin_preserves_leading_zeroes() -> None:
    profile = UserProfile(
        name="Test user",
        profile_pin="0012",
        sex=Sex.FEMALE,
        date_of_birth=date(1990, 1, 1),
        height_cm=165,
    )
    assert profile.profile_pin == "0012"


def test_profile_options_round_trip_is_json_safe_and_preserves_pin() -> None:
    profile = UserProfile(
        name="Test user",
        profile_pin="0012",
        sex=Sex.FEMALE,
        date_of_birth=date(1990, 1, 2),
        height_cm=165.5,
        athlete_mode=True,
    )

    stored = serialize_profiles({profile.profile_pin: profile})
    assert stored == {
        "0012": {
            "profile_id": profile.profile_id,
            "name": "Test user",
            "sex": "female",
            "date_of_birth": "1990-01-02",
            "height_cm": 165.5,
            "athlete_mode": True,
            "reference_standard": "external",
        }
    }
    assert deserialize_profiles({CONF_PROFILES: stored}) == {"0012": profile}


def test_legacy_profile_gets_a_deterministic_stable_id() -> None:
    legacy = {
        "0012": {
            "name": "Test user",
            "sex": "female",
            "date_of_birth": "1990-01-02",
            "height_cm": 165.5,
            "athlete_mode": False,
            "reference_standard": "external",
        }
    }

    first = deserialize_profiles({CONF_PROFILES: legacy})["0012"]
    second = deserialize_profiles({CONF_PROFILES: legacy})["0012"]

    assert first.profile_id == second.profile_id
    assert first.profile_id is not None
    assert len(first.profile_id) == 32
    assert serialize_profiles({"0012": first})["0012"][CONF_PROFILE_ID] == (
        first.profile_id
    )


def test_duplicate_stored_profile_ids_are_rejected() -> None:
    profile = UserProfile(
        name="First",
        profile_pin="0012",
        profile_id="1" * 32,
        sex=Sex.FEMALE,
        date_of_birth=date(1990, 1, 2),
        height_cm=165.5,
    )
    duplicate = UserProfile(
        name="Second",
        profile_pin="0013",
        profile_id="1" * 32,
        sex=Sex.MALE,
        date_of_birth=date(1991, 1, 2),
        height_cm=180,
    )
    stored = serialize_profiles({"0012": profile, "0013": duplicate})

    with pytest.raises(ValueError, match="profile IDs must be unique"):
        deserialize_profiles({CONF_PROFILES: stored})


def test_missing_profile_options_load_as_empty_registry() -> None:
    assert deserialize_profiles({}) == {}


def test_stored_profile_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="invalid stored fields"):
        deserialize_profiles(
            {
                CONF_PROFILES: {
                    "0012": {
                        "name": "Test user",
                        "sex": "female",
                        "date_of_birth": "1990-01-02",
                        "height_cm": 165.5,
                        "athlete_mode": False,
                        "reference_standard": "external",
                        "unexpected": True,
                    }
                }
            }
        )


def test_profile_mapping_key_must_match_pin() -> None:
    profile = UserProfile(
        name="Test user",
        profile_pin="0012",
        sex=Sex.FEMALE,
        date_of_birth=date(1990, 1, 2),
        height_cm=165.5,
    )
    with pytest.raises(ValueError, match="mapping key"):
        serialize_profiles({"9999": profile})


@pytest.mark.parametrize("pin", ["123", "12345", "12A4", 1234])
def test_invalid_profile_pin_is_rejected(pin: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        UserProfile(
            name="Test user",
            profile_pin=pin,  # type: ignore[arg-type]
            sex=Sex.MALE,
            date_of_birth=date(1990, 1, 1),
            height_cm=180,
        )


def test_profile_date_of_birth_must_be_a_date() -> None:
    with pytest.raises(TypeError, match="date_of_birth"):
        UserProfile(
            name="Test user",
            profile_pin="1234",
            sex=Sex.MALE,
            date_of_birth="1990-01-01",  # type: ignore[arg-type]
            height_cm=180,
        )


@pytest.mark.parametrize(
    "profile_id",
    ["", "1" * 31, "1" * 33, "G" * 32, 123],
)
def test_invalid_profile_id_is_rejected(profile_id: object) -> None:
    with pytest.raises(ValueError, match="profile_id"):
        UserProfile(
            name="Test user",
            profile_pin="1234",
            profile_id=profile_id,  # type: ignore[arg-type]
            sex=Sex.MALE,
            date_of_birth=date(1990, 1, 1),
            height_cm=180,
        )
