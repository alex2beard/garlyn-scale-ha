"""The GARLYN Scale integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_SCALE_ID, CONF_WEBHOOK_ID, DOMAIN
from .models import deserialize_profiles
from .runtime import ScaleRuntime
from .storage import RuntimeStateStore

PLATFORMS = ("sensor",)

if TYPE_CHECKING:
    from aiohttp.web import Request, Response
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[ScaleRuntime]
) -> bool:
    """Set up one physical GARLYN scale from a config entry."""
    from homeassistant.components import webhook
    from homeassistant.exceptions import ConfigEntryError

    from .webhook import async_handle_measurement

    try:
        profiles = deserialize_profiles(entry.options)
    except (TypeError, ValueError) as err:
        raise ConfigEntryError("Stored GARLYN profiles are invalid") from err

    runtime = ScaleRuntime(
        scale_id=entry.data[CONF_SCALE_ID],
        profiles=profiles,
    )
    state_store = RuntimeStateStore(hass, entry.entry_id)
    try:
        await state_store.async_load(runtime)
    except (TypeError, ValueError) as err:
        raise ConfigEntryError("Stored GARLYN runtime state is invalid") from err
    entry.runtime_data = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_webhook(
        hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response:
        del hass, webhook_id
        return await async_handle_measurement(
            runtime,
            request,
            state_store.async_save,
        )

    webhook.async_register(
        hass,
        DOMAIN,
        entry.title,
        entry.data[CONF_WEBHOOK_ID],
        handle_webhook,
        local_only=True,
        allowed_methods={"POST"},
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry[ScaleRuntime]
) -> bool:
    """Unload a GARLYN scale config entry."""
    from homeassistant.components import webhook

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant, entry: ConfigEntry[ScaleRuntime]
) -> None:
    """Remove restart-safe state with its config entry."""
    await RuntimeStateStore(hass, entry.entry_id).async_remove()
