"""Profile-specific sensors for one physical GARLYN scale."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, Platform, UnitOfMass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .models import UserProfile
from .runtime import ProcessedMeasurement, ScaleRuntime

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_KILOCALORIES_PER_DAY: Final = "kcal/day"


@dataclass(frozen=True, slots=True)
class GarlynSensorDefinition:
    """Description and value selector for one calculated field."""

    description: SensorEntityDescription
    value_fn: Callable[[ProcessedMeasurement], int | float]


SENSOR_DEFINITIONS: Final = (
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="weight",
            translation_key="profile_weight",
            has_entity_name=True,
            force_update=True,
            device_class=SensorDeviceClass.WEIGHT,
            native_unit_of_measurement=UnitOfMass.KILOGRAMS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
        lambda processed: processed.measurement.weight_kg,
    ),
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="body_fat_percentage",
            translation_key="profile_body_fat_percentage",
            has_entity_name=True,
            force_update=True,
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
        lambda processed: processed.result.body_fat_pct,
    ),
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="body_fat_mass",
            translation_key="profile_body_fat_mass",
            has_entity_name=True,
            force_update=True,
            device_class=SensorDeviceClass.WEIGHT,
            native_unit_of_measurement=UnitOfMass.KILOGRAMS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
        lambda processed: processed.result.body_fat_kg,
    ),
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="muscle_percentage",
            translation_key="profile_muscle_percentage",
            has_entity_name=True,
            force_update=True,
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
        lambda processed: processed.result.muscle_pct,
    ),
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="muscle_mass",
            translation_key="profile_muscle_mass",
            has_entity_name=True,
            force_update=True,
            device_class=SensorDeviceClass.WEIGHT,
            native_unit_of_measurement=UnitOfMass.KILOGRAMS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
        lambda processed: processed.result.muscle_kg,
    ),
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="body_water_percentage",
            translation_key="profile_body_water_percentage",
            has_entity_name=True,
            force_update=True,
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
        lambda processed: processed.result.body_water_pct,
    ),
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="body_water_mass",
            translation_key="profile_body_water_mass",
            has_entity_name=True,
            force_update=True,
            device_class=SensorDeviceClass.WEIGHT,
            native_unit_of_measurement=UnitOfMass.KILOGRAMS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
        lambda processed: processed.result.body_water_kg,
    ),
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="basal_metabolic_rate",
            translation_key="profile_basal_metabolic_rate",
            has_entity_name=True,
            force_update=True,
            native_unit_of_measurement=_KILOCALORIES_PER_DAY,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
        lambda processed: processed.result.bmr_kcal,
    ),
    GarlynSensorDefinition(
        SensorEntityDescription(
            key="body_mass_index",
            translation_key="profile_body_mass_index",
            has_entity_name=True,
            force_update=True,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
        lambda processed: processed.result.bmi,
    ),
)


def _scale_device_info(entry: ConfigEntry, runtime: ScaleRuntime) -> DeviceInfo:
    """Return the shared physical device descriptor."""
    return DeviceInfo(
        identifiers={(DOMAIN, runtime.scale_id)},
        name=entry.title,
        manufacturer="GARLYN",
        model="Bodyscan Master",
    )


def _unique_id(runtime: ScaleRuntime, profile: UserProfile, key: str) -> str:
    """Build a stable sensor ID independent of mutable PIN and name."""
    assert profile.profile_id is not None
    return f"{runtime.scale_id}:{profile.profile_id}:{key}"


@callback
def _async_remove_stale_profile_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: ScaleRuntime,
) -> None:
    """Remove registry entities belonging to profiles deleted by the user."""
    active_profile_ids = {profile.profile_id for profile in runtime.profiles.values()}
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registry_entry.domain != Platform.SENSOR
            or registry_entry.platform != DOMAIN
        ):
            continue
        unique_id = registry_entry.unique_id
        parts = unique_id.split(":", 2)
        if len(parts) != 3 or parts[0] != runtime.scale_id:
            continue
        if parts[1] not in active_profile_ids:
            registry.async_remove(registry_entry.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ScaleRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all configured profile sensors."""
    runtime = entry.runtime_data
    device_info = _scale_device_info(entry, runtime)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **device_info,
    )
    _async_remove_stale_profile_entities(hass, entry, runtime)

    async_add_entities(
        GarlynProfileSensor(runtime, profile, device_info, definition)
        for profile_pin in sorted(runtime.profiles)
        for profile in (runtime.profiles[profile_pin],)
        for definition in SENSOR_DEFINITIONS
    )


class GarlynProfileSensor(SensorEntity):
    """One current value for one configured GARLYN user profile."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_force_update = True

    def __init__(
        self,
        runtime: ScaleRuntime,
        profile: UserProfile,
        device_info: DeviceInfo,
        definition: GarlynSensorDefinition,
    ) -> None:
        """Initialize a profile sensor."""
        assert profile.profile_id is not None
        self.entity_description = definition.description
        self._runtime = runtime
        self._profile = profile
        self._profile_id = profile.profile_id
        self._value_fn = definition.value_fn
        self._attr_unique_id = _unique_id(runtime, profile, definition.description.key)
        self._attr_device_info = device_info
        self._attr_translation_placeholders = {"profile_name": profile.name}

    @property
    def native_value(self) -> int | float | None:
        """Return the latest persisted value for this stable profile."""
        if processed := self._runtime.latest_for_profile(self._profile):
            return self._value_fn(processed)
        return None

    async def async_added_to_hass(self) -> None:
        """Subscribe only while the entity is active in Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(self._runtime.add_listener(self._handle_runtime_update))

    @callback
    def _handle_runtime_update(self, processed: ProcessedMeasurement) -> None:
        """Write state only for a new measurement of this profile."""
        if processed.user_profile.profile_id == self._profile_id:
            self.async_write_ha_state()
