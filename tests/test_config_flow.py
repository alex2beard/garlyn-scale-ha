"""Focused tests for profile options-flow state transitions."""

from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from custom_components.garlyn_scale.const import (
    CONF_PROFILES,
    CONF_SCALE_ID,
    CONF_WEBHOOK_ID,
)
from custom_components.garlyn_scale.models import deserialize_profiles


class _FlowBase:
    """Small Home Assistant data-entry-flow surface used by these unit tests."""

    def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.domain = domain

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "menu", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "abort", **kwargs}


class _Selector:
    def __init__(self, config: object | None = None) -> None:
        self.config = config

    def __call__(self, value: object) -> object:
        return value


class _SelectorConfig(dict[str, Any]):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


@pytest.fixture
def config_flow_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import config_flow against a minimal current-API-compatible HA shell."""
    homeassistant = ModuleType("homeassistant")
    components = ModuleType("homeassistant.components")
    webhook = ModuleType("homeassistant.components.webhook")
    webhook.async_generate_id = lambda: "webhook-id"  # type: ignore[attr-defined]
    webhook.async_generate_path = lambda value: f"/api/webhook/{value}"  # type: ignore[attr-defined]
    components.webhook = webhook  # type: ignore[attr-defined]

    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = SimpleNamespace  # type: ignore[attr-defined]
    config_entries.ConfigFlow = _FlowBase  # type: ignore[attr-defined]
    config_entries.ConfigFlowResult = dict  # type: ignore[attr-defined]
    config_entries.OptionsFlowWithReload = _FlowBase  # type: ignore[attr-defined]

    core = ModuleType("homeassistant.core")
    core.callback = lambda function: function  # type: ignore[attr-defined]

    helpers = ModuleType("homeassistant.helpers")
    selector = ModuleType("homeassistant.helpers.selector")
    selector.DateSelector = _Selector  # type: ignore[attr-defined]
    selector.NumberSelector = _Selector  # type: ignore[attr-defined]
    selector.NumberSelectorConfig = _SelectorConfig  # type: ignore[attr-defined]
    selector.NumberSelectorMode = SimpleNamespace(BOX="box")  # type: ignore[attr-defined]
    selector.SelectOptionDict = _SelectorConfig  # type: ignore[attr-defined]
    selector.SelectSelector = _Selector  # type: ignore[attr-defined]
    selector.SelectSelectorConfig = _SelectorConfig  # type: ignore[attr-defined]
    selector.SelectSelectorMode = SimpleNamespace(DROPDOWN="dropdown")  # type: ignore[attr-defined]
    helpers.selector = selector  # type: ignore[attr-defined]

    modules = {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.webhook": webhook,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.selector": selector,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    module_name = "custom_components.garlyn_scale.config_flow"
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    yield module
    sys.modules.pop(module_name, None)


def _profile_input(*, profile_pin: str = "4242") -> dict[str, object]:
    return {
        "name": "Synthetic profile",
        "profile_pin": profile_pin,
        "sex": "female",
        "date_of_birth": "1991-06-15",
        "height_cm": 175,
        "athlete_mode": False,
        "reference_standard": "external",
    }


def _flow(module: ModuleType, options: dict[str, object]) -> object:
    flow = module.GarlynScaleOptionsFlow()  # type: ignore[attr-defined]
    flow.config_entry = SimpleNamespace(
        data={CONF_SCALE_ID: "scale-id", CONF_WEBHOOK_ID: "webhook-id"},
        options=options,
    )
    return flow


def test_empty_options_menu_and_add_profile(config_flow_module: ModuleType) -> None:
    flow = _flow(config_flow_module, {})

    menu = asyncio.run(flow.async_step_init())  # type: ignore[attr-defined]
    assert menu["type"] == "menu"
    assert menu["menu_options"] == ["connection_info", "add_profile"]
    assert menu["description_placeholders"] == {"profile_count": "0"}

    connection_info = asyncio.run(  # type: ignore[attr-defined]
        flow.async_step_connection_info()
    )
    assert connection_info["type"] == "menu"
    assert connection_info["step_id"] == "connection_info"
    assert connection_info["menu_options"] == ["init"]
    assert connection_info["description_placeholders"] == {
        "scale_id": "scale-id",
        "webhook_path": "/api/webhook/webhook-id",
    }

    result = asyncio.run(  # type: ignore[attr-defined]
        flow.async_step_add_profile(_profile_input())
    )
    assert result["type"] == "create_entry"
    profiles = deserialize_profiles(result["data"])
    assert profiles["4242"].name == "Synthetic profile"
    assert profiles["4242"].athlete_mode is False


def test_duplicate_pin_is_rejected(config_flow_module: ModuleType) -> None:
    initial = asyncio.run(  # type: ignore[attr-defined]
        _flow(config_flow_module, {}).async_step_add_profile(_profile_input())
    )
    flow = _flow(config_flow_module, initial["data"])

    duplicate = asyncio.run(  # type: ignore[attr-defined]
        flow.async_step_add_profile(_profile_input())
    )
    assert duplicate["type"] == "form"
    assert duplicate["errors"] == {"profile_pin": "pin_already_configured"}


@pytest.mark.parametrize("profile_pin", ["123", "12345", "12ab"])
def test_invalid_pin_is_rejected(
    config_flow_module: ModuleType, profile_pin: str
) -> None:
    flow = _flow(config_flow_module, {})

    result = asyncio.run(  # type: ignore[attr-defined]
        flow.async_step_add_profile(_profile_input(profile_pin=profile_pin))
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_profile"}


def test_edit_can_change_pin_and_delete_requires_confirmation(
    config_flow_module: ModuleType,
) -> None:
    initial = asyncio.run(  # type: ignore[attr-defined]
        _flow(config_flow_module, {}).async_step_add_profile(_profile_input())
    )
    original_profile_id = deserialize_profiles(initial["data"])["4242"].profile_id
    edit_flow = _flow(config_flow_module, initial["data"])

    details = asyncio.run(  # type: ignore[attr-defined]
        edit_flow.async_step_edit_profile({"profile_pin": "4242"})
    )
    assert details["step_id"] == "edit_profile_details"
    changed = asyncio.run(  # type: ignore[attr-defined]
        edit_flow.async_step_edit_profile_details(_profile_input(profile_pin="0012"))
    )
    assert "4242" not in changed["data"][CONF_PROFILES]
    changed_profile = deserialize_profiles(changed["data"])["0012"]
    assert changed_profile.name == "Synthetic profile"
    assert changed_profile.profile_id == original_profile_id

    delete_flow = _flow(config_flow_module, changed["data"])
    confirmation = asyncio.run(  # type: ignore[attr-defined]
        delete_flow.async_step_delete_profile({"profile_pin": "0012"})
    )
    assert confirmation["step_id"] == "confirm_delete_profile"
    deleted = asyncio.run(  # type: ignore[attr-defined]
        delete_flow.async_step_confirm_delete_profile({})
    )
    assert deserialize_profiles(deleted["data"]) == {}

    readded = asyncio.run(  # type: ignore[attr-defined]
        _flow(config_flow_module, deleted["data"]).async_step_add_profile(
            _profile_input(profile_pin="0012")
        )
    )
    assert (
        deserialize_profiles(readded["data"])["0012"].profile_id != original_profile_id
    )
