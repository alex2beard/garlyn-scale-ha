"""Tests for config-entry setup wiring."""

import asyncio
import sys
from datetime import date
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest

from custom_components.garlyn_scale import (
    PLATFORMS,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.garlyn_scale.algorithm import Sex
from custom_components.garlyn_scale.const import (
    CONF_PROFILES,
    CONF_SCALE_ID,
    CONF_SPARKY_URL,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from custom_components.garlyn_scale.models import UserProfile, serialize_profiles


class FakeConfigEntryError(Exception):
    """Stand-in for Home Assistant's setup exception."""


class FakeStore:
    """Minimal stand-in for Home Assistant's versioned Store helper."""

    instances: ClassVar[list["FakeStore"]] = []
    next_load_data: object | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.saved: list[object] = []
        self.removed = False
        self.instances.append(self)

    async def async_load(self) -> object | None:
        return self.next_load_data

    async def async_save(self, data: object) -> None:
        self.saved.append(data)

    async def async_remove(self) -> None:
        self.removed = True


class FakeRequest:
    """Minimal JSON request for the registered webhook callback."""

    content_length = None

    async def json(self) -> dict[str, object]:
        return {
            "protocol_version": 1,
            "scale_id": "scale-1",
            "measurement_id": "measurement-1",
            "measured_at": "2026-01-15T12:00:00+00:00",
            "profile_pin": "4242",
            "weight_kg": 74.8,
            "bia": {
                "20khz": [410.2, 408.6, 360.4, 355.9, 30.1],
                "100khz": [365.1, 363.8, 315.6, 312.2, 26.5],
            },
        }


class FakeConfigEntries:
    """Record config-entry platform lifecycle calls."""

    def __init__(self, *, unload_result: bool = True) -> None:
        self.forwarded: list[tuple[object, tuple[str, ...]]] = []
        self.unloaded: list[tuple[object, tuple[str, ...]]] = []
        self.unload_result = unload_result

    async def async_forward_entry_setups(
        self, entry: object, platforms: tuple[str, ...]
    ) -> None:
        self.forwarded.append((entry, platforms))

    async def async_unload_platforms(
        self, entry: object, platforms: tuple[str, ...]
    ) -> bool:
        self.unloaded.append((entry, platforms))
        return self.unload_result


class FakeHass:
    """Minimal Home Assistant surface used by config-entry setup."""

    def __init__(self, *, unload_result: bool = True) -> None:
        self.config_entries = FakeConfigEntries(unload_result=unload_result)
        self.data: dict[str, object] = {}
        self.config = SimpleNamespace(time_zone="UTC")


def _install_home_assistant_stubs(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]], type[FakeStore]]:
    registrations: list[tuple[object, ...]] = []
    unregistrations: list[tuple[object, ...]] = []
    FakeStore.instances = []
    FakeStore.next_load_data = None
    homeassistant = ModuleType("homeassistant")
    components = ModuleType("homeassistant.components")
    webhook = ModuleType("homeassistant.components.webhook")

    def async_register(*args: object, **kwargs: object) -> None:
        registrations.append((*args, kwargs))

    def async_unregister(*args: object) -> None:
        unregistrations.append(args)

    webhook.async_register = async_register  # type: ignore[attr-defined]
    webhook.async_unregister = async_unregister  # type: ignore[attr-defined]
    components.webhook = webhook  # type: ignore[attr-defined]
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryError = FakeConfigEntryError  # type: ignore[attr-defined]
    helpers = ModuleType("homeassistant.helpers")
    aiohttp_client = ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: object()  # type: ignore[attr-defined]
    storage = ModuleType("homeassistant.helpers.storage")
    storage.Store = FakeStore  # type: ignore[attr-defined]
    helpers.aiohttp_client = aiohttp_client  # type: ignore[attr-defined]

    modules = {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.webhook": webhook,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": aiohttp_client,
        "homeassistant.helpers.storage": storage,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return registrations, unregistrations, FakeStore


def _profile() -> UserProfile:
    return UserProfile(
        name="Synthetic profile",
        profile_pin="4242",
        sex=Sex.FEMALE,
        date_of_birth=date(1991, 6, 15),
        height_cm=175,
        athlete_mode=False,
    )


def test_setup_loads_profiles_and_registers_local_post_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registrations, _, store_class = _install_home_assistant_stubs(monkeypatch)
    profile = _profile()
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_SCALE_ID: "scale-1", CONF_WEBHOOK_ID: "webhook-1"},
        options={CONF_PROFILES: serialize_profiles({"4242": profile})},
        title="Bathroom scale",
        runtime_data=None,
    )
    hass = FakeHass()

    assert asyncio.run(async_setup_entry(hass, entry)) is True
    assert entry.runtime_data.profiles == {"4242": profile}
    assert hass.config_entries.forwarded == [(entry, PLATFORMS)]
    assert len(store_class.instances) == 1
    assert store_class.instances[0].args == (
        hass,
        1,
        "garlyn_scale.runtime.entry-1",
    )
    assert store_class.instances[0].kwargs == {
        "private": True,
        "atomic_writes": True,
        "minor_version": 2,
    }
    assert len(registrations) == 1
    registration = registrations[0]
    assert registration[:4] == (
        hass,
        "garlyn_scale",
        "Bathroom scale",
        "webhook-1",
    )
    assert callable(registration[4])
    assert registration[5] == {
        "local_only": True,
        "allowed_methods": {"POST"},
    }


def test_setup_rejects_corrupt_profile_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_home_assistant_stubs(monkeypatch)
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_SCALE_ID: "scale-1", CONF_WEBHOOK_ID: "webhook-1"},
        options={CONF_PROFILES: []},
        title="Bathroom scale",
        runtime_data=None,
    )

    with pytest.raises(FakeConfigEntryError, match="Stored GARLYN profiles"):
        asyncio.run(async_setup_entry(FakeHass(), entry))


def test_setup_wires_optional_sparky_manager_only_for_enabled_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_home_assistant_stubs(monkeypatch)
    profile = UserProfile(
        name="Synthetic profile",
        profile_pin="4242",
        sex=Sex.FEMALE,
        date_of_birth=date(1991, 6, 15),
        height_cm=175,
        sparky_enabled=True,
        sparky_api_key="secret-token",
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_SCALE_ID: "scale-1", CONF_WEBHOOK_ID: "webhook-1"},
        options={
            CONF_PROFILES: serialize_profiles({"4242": profile}),
            CONF_SPARKY_URL: "https://sparky.example.com",
        },
        title="Bathroom scale",
        runtime_data=None,
        async_create_background_task=lambda *args, **kwargs: pytest.fail(
            f"empty outbox unexpectedly created a task: {args}, {kwargs}"
        ),
    )
    hass = FakeHass()

    assert asyncio.run(async_setup_entry(hass, entry)) is True
    assert entry.entry_id in hass.data[DOMAIN]


def test_setup_rejects_enabled_sparky_profile_without_valid_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_home_assistant_stubs(monkeypatch)
    profile = UserProfile(
        name="Synthetic profile",
        profile_pin="4242",
        sex=Sex.FEMALE,
        date_of_birth=date(1991, 6, 15),
        height_cm=175,
        sparky_enabled=True,
        sparky_api_key="secret-token",
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_SCALE_ID: "scale-1", CONF_WEBHOOK_ID: "webhook-1"},
        options={CONF_PROFILES: serialize_profiles({"4242": profile})},
        title="Bathroom scale",
        runtime_data=None,
    )

    with pytest.raises(FakeConfigEntryError, match="Sparky URL is required"):
        asyncio.run(async_setup_entry(FakeHass(), entry))


def test_webhook_retry_remains_duplicate_after_entry_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registrations, _, store_class = _install_home_assistant_stubs(monkeypatch)
    profile = _profile()
    entry_data = {CONF_SCALE_ID: "scale-1", CONF_WEBHOOK_ID: "webhook-1"}
    entry_options = {CONF_PROFILES: serialize_profiles({"4242": profile})}
    hass = FakeHass()

    first_entry = SimpleNamespace(
        entry_id="entry-1",
        data=entry_data,
        options=entry_options,
        title="Bathroom scale",
        runtime_data=None,
    )
    assert asyncio.run(async_setup_entry(hass, first_entry)) is True
    first_handler = registrations[0][4]
    accepted = asyncio.run(first_handler(hass, "webhook-1", FakeRequest()))
    assert accepted.status == 202
    assert len(store_class.instances[0].saved) == 1

    store_class.next_load_data = store_class.instances[0].saved[0]
    restarted_entry = SimpleNamespace(
        entry_id="entry-1",
        data=entry_data,
        options=entry_options,
        title="Bathroom scale",
        runtime_data=None,
    )
    assert asyncio.run(async_setup_entry(hass, restarted_entry)) is True
    restarted_handler = registrations[1][4]
    duplicate = asyncio.run(restarted_handler(hass, "webhook-1", FakeRequest()))

    assert duplicate.status == 200
    assert restarted_entry.runtime_data.seen_count == 1
    assert len(store_class.instances[1].saved) == 1


def test_setup_rejects_invalid_persistent_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, store_class = _install_home_assistant_stubs(monkeypatch)
    store_class.next_load_data = {"wrong": "shape"}
    profile = _profile()
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_SCALE_ID: "scale-1", CONF_WEBHOOK_ID: "webhook-1"},
        options={CONF_PROFILES: serialize_profiles({"4242": profile})},
        title="Bathroom scale",
        runtime_data=None,
    )

    with pytest.raises(FakeConfigEntryError, match="runtime state is invalid"):
        asyncio.run(async_setup_entry(FakeHass(), entry))


def test_remove_entry_deletes_persistent_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, store_class = _install_home_assistant_stubs(monkeypatch)
    hass = object()
    entry = SimpleNamespace(entry_id="entry-1")

    asyncio.run(async_remove_entry(hass, entry))

    assert len(store_class.instances) == 1
    assert store_class.instances[0].removed is True


@pytest.mark.parametrize("unload_result", [True, False])
def test_unload_entry_respects_platform_result_before_removing_webhook(
    monkeypatch: pytest.MonkeyPatch,
    unload_result: bool,
) -> None:
    _, unregistrations, _ = _install_home_assistant_stubs(monkeypatch)
    hass = FakeHass(unload_result=unload_result)
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_WEBHOOK_ID: "webhook-1"},
        runtime_data=None,
    )

    assert asyncio.run(async_unload_entry(hass, entry)) is unload_result
    assert hass.config_entries.unloaded == [(entry, PLATFORMS)]
    assert unregistrations == ([(hass, "webhook-1")] if unload_result else [])
