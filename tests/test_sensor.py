"""Tests for profile sensors and their Home Assistant lifecycle."""

from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import date
from enum import StrEnum
from types import ModuleType, SimpleNamespace

import pytest

from custom_components.garlyn_scale.algorithm import Sex
from custom_components.garlyn_scale.models import UserProfile
from custom_components.garlyn_scale.runtime import AcceptanceStatus, ScaleRuntime
from custom_components.garlyn_scale.storage import (
    restore_runtime_state,
    serialize_runtime_state,
)
from custom_components.garlyn_scale.transport import Measurement, parse_measurement


class FakeSensorDeviceClass(StrEnum):
    """Sensor device classes used by the integration."""

    WEIGHT = "weight"


class FakeSensorStateClass(StrEnum):
    """Sensor state classes used by the integration."""

    MEASUREMENT = "measurement"


class FakePlatform(StrEnum):
    """Home Assistant platforms used by the integration."""

    SENSOR = "sensor"


class FakeUnitOfMass(StrEnum):
    """Mass units used by the integration."""

    KILOGRAMS = "kg"


class FakeSensorEntityDescription:
    """Store entity-description keyword arguments as attributes."""

    def __init__(self, *, key: str, **kwargs: object) -> None:
        self.key = key
        for name, value in kwargs.items():
            setattr(self, name, value)


class FakeSensorEntity:
    """Minimal push-entity lifecycle surface."""

    async def async_added_to_hass(self) -> None:
        """Mirror the no-op base lifecycle hook."""

    def async_on_remove(self, callback: object) -> None:
        callbacks = getattr(self, "remove_callbacks", [])
        callbacks.append(callback)
        self.remove_callbacks = callbacks

    def async_write_ha_state(self) -> None:
        self.write_count = getattr(self, "write_count", 0) + 1


class FakeDeviceRegistry:
    """Record physical-device creation calls."""

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def async_get_or_create(self, **kwargs: object) -> object:
        self.created.append(kwargs)
        return SimpleNamespace(**kwargs)


class FakeEntityRegistry:
    """Expose config-entry entities and record removals."""

    def __init__(self, entries: list[object] | None = None) -> None:
        self.entries = entries or []
        self.removed: list[str] = []

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)


class FakeHass:
    """Registry container used by the Home Assistant stubs."""

    def __init__(self, entries: list[object] | None = None) -> None:
        self.device_registry = FakeDeviceRegistry()
        self.entity_registry = FakeEntityRegistry(entries)


@pytest.fixture
def sensor_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the sensor platform against a focused Home Assistant shell."""
    homeassistant = ModuleType("homeassistant")
    components = ModuleType("homeassistant.components")
    sensor = ModuleType("homeassistant.components.sensor")
    sensor.SensorDeviceClass = FakeSensorDeviceClass  # type: ignore[attr-defined]
    sensor.SensorEntity = FakeSensorEntity  # type: ignore[attr-defined]
    sensor.SensorEntityDescription = FakeSensorEntityDescription  # type: ignore[attr-defined]
    sensor.SensorStateClass = FakeSensorStateClass  # type: ignore[attr-defined]
    components.sensor = sensor  # type: ignore[attr-defined]

    const = ModuleType("homeassistant.const")
    const.PERCENTAGE = "%"  # type: ignore[attr-defined]
    const.Platform = FakePlatform  # type: ignore[attr-defined]
    const.UnitOfMass = FakeUnitOfMass  # type: ignore[attr-defined]

    core = ModuleType("homeassistant.core")
    core.HomeAssistant = object  # type: ignore[attr-defined]
    core.callback = lambda function: function  # type: ignore[attr-defined]

    helpers = ModuleType("homeassistant.helpers")
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    device_registry.DeviceInfo = dict  # type: ignore[attr-defined]
    device_registry.async_get = lambda hass: hass.device_registry  # type: ignore[attr-defined]
    entity_registry = ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: hass.entity_registry  # type: ignore[attr-defined]
    entity_registry.async_entries_for_config_entry = (  # type: ignore[attr-defined]
        lambda registry, entry_id: [
            entry for entry in registry.entries if entry.config_entry_id == entry_id
        ]
    )
    entity_platform = ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddConfigEntryEntitiesCallback = object  # type: ignore[attr-defined]
    helpers.device_registry = device_registry  # type: ignore[attr-defined]
    helpers.entity_registry = entity_registry  # type: ignore[attr-defined]

    modules = {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.sensor": sensor,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.device_registry": device_registry,
        "homeassistant.helpers.entity_registry": entity_registry,
        "homeassistant.helpers.entity_platform": entity_platform,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    module_name = "custom_components.garlyn_scale.sensor"
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    yield module
    sys.modules.pop(module_name, None)


def _profile(
    *,
    profile_pin: str = "4242",
    profile_id: str = "a" * 32,
) -> UserProfile:
    return UserProfile(
        name="Synthetic profile",
        profile_pin=profile_pin,
        profile_id=profile_id,
        sex=Sex.FEMALE,
        date_of_birth=date(1991, 6, 15),
        height_cm=175,
        athlete_mode=False,
    )


def _measurement(measurement_id: str) -> Measurement:
    return parse_measurement(
        {
            "protocol_version": 1,
            "scale_id": "scale-1",
            "measurement_id": measurement_id,
            "measured_at": "2026-01-15T12:00:00+00:00",
            "profile_pin": "4242",
            "weight_kg": 74.8,
            "bia": {
                "20khz": [410.2, 408.6, 360.4, 355.9, 30.1],
                "100khz": [365.1, 363.8, 315.6, 312.2, 26.5],
            },
        }
    )


def _entry(runtime: ScaleRuntime) -> object:
    return SimpleNamespace(
        entry_id="entry-1",
        title="Bathroom scale",
        runtime_data=runtime,
    )


def _setup_entities(module: ModuleType, hass: FakeHass, runtime: ScaleRuntime) -> list:
    entities: list = []
    asyncio.run(module.async_setup_entry(hass, _entry(runtime), entities.extend))
    return entities


def test_setup_creates_one_device_and_nine_stable_profile_sensors(
    sensor_module: ModuleType,
) -> None:
    profile = _profile()
    runtime = ScaleRuntime("scale-1", profiles={"4242": profile})
    hass = FakeHass()

    entities = _setup_entities(sensor_module, hass, runtime)

    assert hass.device_registry.created == [
        {
            "config_entry_id": "entry-1",
            "identifiers": {("garlyn_scale", "scale-1")},
            "name": "Bathroom scale",
            "manufacturer": "GARLYN",
            "model": "Bodyscan Master",
        }
    ]
    assert len(entities) == 9
    assert {entity.entity_description.key for entity in entities} == {
        "weight",
        "body_fat_percentage",
        "body_fat_mass",
        "muscle_percentage",
        "muscle_mass",
        "body_water_percentage",
        "body_water_mass",
        "basal_metabolic_rate",
        "body_mass_index",
    }
    assert {entity._attr_unique_id for entity in entities} == {
        f"scale-1:{profile.profile_id}:{entity.entity_description.key}"
        for entity in entities
    }
    assert all(
        entity._attr_translation_placeholders == {"profile_name": "Synthetic profile"}
        for entity in entities
    )
    assert all(
        entity.entity_description.state_class is FakeSensorStateClass.MEASUREMENT
        for entity in entities
    )
    assert all(entity.native_value is None for entity in entities)


def test_push_updates_only_once_per_measurement_and_unsubscribes(
    sensor_module: ModuleType,
) -> None:
    profile = _profile()
    runtime = ScaleRuntime("scale-1", profiles={"4242": profile})
    entities = _setup_entities(sensor_module, FakeHass(), runtime)
    for entity in entities:
        asyncio.run(entity.async_added_to_hass())
    assert runtime.listener_count == 9

    first = _measurement("measurement-1")
    assert runtime.process(first) is AcceptanceStatus.ACCEPTED
    runtime.publish_last_processed()

    values = {entity.entity_description.key: entity.native_value for entity in entities}
    assert values == {
        "weight": 74.8,
        "body_fat_percentage": 32.80478286743164,
        "body_fat_mass": 24.537979125976562,
        "muscle_percentage": 62.367652893066406,
        "muscle_mass": 46.65100860595703,
        "body_water_percentage": 49.12552261352539,
        "body_water_mass": 36.74589157104492,
        "basal_metabolic_rate": 1455,
        "body_mass_index": 24.424489974975586,
    }
    assert all(entity.write_count == 1 for entity in entities)

    assert runtime.process(first) is AcceptanceStatus.DUPLICATE
    runtime.publish_last_processed()
    assert all(entity.write_count == 1 for entity in entities)

    assert runtime.process(_measurement("measurement-2")) is AcceptanceStatus.ACCEPTED
    runtime.publish_last_processed()
    assert all(entity.write_count == 2 for entity in entities)

    for entity in entities:
        for remove_listener in entity.remove_callbacks:
            remove_listener()
    assert runtime.listener_count == 0


def test_restored_values_are_available_without_a_synthetic_update(
    sensor_module: ModuleType,
) -> None:
    profile = _profile()
    original = ScaleRuntime("scale-1", profiles={"4242": profile})
    original.process(_measurement("measurement-1"))

    restored = ScaleRuntime("scale-1", profiles={"4242": profile})
    restore_runtime_state(restored, serialize_runtime_state(original))
    entities = _setup_entities(sensor_module, FakeHass(), restored)

    weight = next(
        entity for entity in entities if entity.entity_description.key == "weight"
    )
    assert weight.native_value == 74.8


def test_deleted_profile_entities_are_removed_selectively(
    sensor_module: ModuleType,
) -> None:
    profile = _profile()
    runtime = ScaleRuntime("scale-1", profiles={"4242": profile})
    deleted_id = "b" * 32
    entries = [
        SimpleNamespace(
            config_entry_id="entry-1",
            domain="sensor",
            platform="garlyn_scale",
            unique_id=f"scale-1:{profile.profile_id}:weight",
            entity_id="sensor.active",
        ),
        SimpleNamespace(
            config_entry_id="entry-1",
            domain="sensor",
            platform="garlyn_scale",
            unique_id=f"scale-1:{deleted_id}:weight",
            entity_id="sensor.deleted",
        ),
        SimpleNamespace(
            config_entry_id="entry-1",
            domain="sensor",
            platform="garlyn_scale",
            unique_id=f"another-scale:{deleted_id}:weight",
            entity_id="sensor.another_scale",
        ),
        SimpleNamespace(
            config_entry_id="entry-1",
            domain="sensor",
            platform="another_integration",
            unique_id=f"scale-1:{deleted_id}:weight",
            entity_id="sensor.another_integration",
        ),
        SimpleNamespace(
            config_entry_id="entry-1",
            domain="binary_sensor",
            platform="garlyn_scale",
            unique_id=f"scale-1:{deleted_id}:connected",
            entity_id="binary_sensor.connected",
        ),
    ]
    hass = FakeHass(entries)

    _setup_entities(sensor_module, hass, runtime)

    assert hass.entity_registry.removed == ["sensor.deleted"]


def test_device_exists_before_any_profile_is_configured(
    sensor_module: ModuleType,
) -> None:
    runtime = ScaleRuntime("scale-1")
    hass = FakeHass()

    assert _setup_entities(sensor_module, hass, runtime) == []
    assert len(hass.device_registry.created) == 1
