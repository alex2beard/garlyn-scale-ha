"""The GARLYN Scale integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from .const import CONF_SCALE_ID, CONF_SPARKY_URL, CONF_WEBHOOK_ID, DOMAIN
from .models import deserialize_profiles
from .runtime import ProcessedMeasurement, ScaleRuntime
from .sparky import (
    SparkyClient,
    SparkyQueueItem,
    SparkySyncManager,
    normalize_sparky_url,
)
from .storage import RuntimeStateStore

PLATFORMS = ("sensor",)

if TYPE_CHECKING:
    from aiohttp.web import Request, Response
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


type _DomainData = dict[str, SparkySyncManager]


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

    raw_sparky_url = entry.options.get(CONF_SPARKY_URL)
    try:
        sparky_url = (
            None
            if raw_sparky_url in (None, "")
            else normalize_sparky_url(raw_sparky_url)
        )
    except ValueError as err:
        raise ConfigEntryError("Stored GARLYN Sparky URL is invalid") from err
    if any(profile.sparky_enabled for profile in profiles.values()) and (
        sparky_url is None
    ):
        raise ConfigEntryError("Sparky URL is required for enabled profiles")

    runtime = ScaleRuntime(
        scale_id=entry.data[CONF_SCALE_ID],
        profiles=profiles,
    )
    state_store = RuntimeStateStore(hass, entry.entry_id)
    try:
        outbox = await state_store.async_load(runtime)
    except (TypeError, ValueError) as err:
        raise ConfigEntryError("Stored GARLYN runtime state is invalid") from err
    if outbox.prune(profiles):
        await state_store.async_save(runtime, outbox)
    entry.runtime_data = runtime

    state_lock = asyncio.Lock()
    sparky_manager: SparkySyncManager | None = None
    if sparky_url is not None and any(
        profile.sparky_enabled for profile in profiles.values()
    ):
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        async def save_all_state() -> None:
            await state_store.async_save(runtime, outbox)

        sparky_manager = SparkySyncManager(
            outbox=outbox,
            profiles=profiles,
            client=SparkyClient(async_get_clientsession(hass), sparky_url),
            state_lock=state_lock,
            async_save_state=save_all_state,
            task_factory=lambda coroutine: entry.async_create_background_task(
                hass,
                coroutine,
                f"{DOMAIN} Sparky outbox",
                eager_start=False,
            ),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    def prepare_sparky_state(
        processed: ProcessedMeasurement,
    ) -> Callable[[], None] | None:
        if not processed.user_profile.sparky_enabled:
            return None
        checkpoint = outbox.checkpoint()
        changed = outbox.enqueue(
            SparkyQueueItem.from_processed(
                processed,
                timezone_name=hass.config.time_zone,
            )
        )
        if not changed:
            return None
        return lambda: outbox.restore_checkpoint(checkpoint)

    async def save_webhook_state(state: ScaleRuntime) -> None:
        await state_store.async_save(state, outbox)

    async def handle_webhook(
        hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response:
        del hass, webhook_id
        response = await async_handle_measurement(
            runtime,
            request,
            save_webhook_state,
            prepare_sparky_state,
            state_lock,
        )
        if sparky_manager is not None:
            sparky_manager.wake()
        return response

    webhook.async_register(
        hass,
        DOMAIN,
        entry.title,
        entry.data[CONF_WEBHOOK_ID],
        handle_webhook,
        local_only=True,
        allowed_methods={"POST"},
    )
    if sparky_manager is not None:
        domain_data: _DomainData = hass.data.setdefault(DOMAIN, {})
        domain_data[entry.entry_id] = sparky_manager
        sparky_manager.wake()
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry[ScaleRuntime]
) -> bool:
    """Unload a GARLYN scale config entry."""
    from homeassistant.components import webhook

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data: _DomainData = hass.data.get(DOMAIN, {})
        if (sparky_manager := domain_data.pop(entry.entry_id, None)) is not None:
            await sparky_manager.async_stop()
        if not domain_data:
            hass.data.pop(DOMAIN, None)
        webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant, entry: ConfigEntry[ScaleRuntime]
) -> None:
    """Remove restart-safe state with its config entry."""
    await RuntimeStateStore(hass, entry.entry_id).async_remove()
