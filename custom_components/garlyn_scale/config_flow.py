"""Config flow for the GARLYN Scale integration."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    DateSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .algorithm import ReferenceStandard, Sex
from .const import (
    CONF_ATHLETE_MODE,
    CONF_DATE_OF_BIRTH,
    CONF_HEIGHT_CM,
    CONF_PROFILE_NAME,
    CONF_PROFILE_PIN,
    CONF_PROFILES,
    CONF_REFERENCE_STANDARD,
    CONF_SCALE_ID,
    CONF_SCALE_NAME,
    CONF_SEX,
    CONF_WEBHOOK_ID,
    DEFAULT_SCALE_NAME,
    DOMAIN,
)
from .models import UserProfile, age_on, deserialize_profiles, serialize_profiles


def _profile_form_schema(profile: UserProfile | None = None) -> vol.Schema:
    """Build the add/edit form, with existing values when editing."""
    defaults: dict[str, object] = {}
    if profile is not None:
        defaults = {
            CONF_PROFILE_NAME: profile.name,
            CONF_PROFILE_PIN: profile.profile_pin,
            CONF_SEX: profile.sex.name.lower(),
            CONF_DATE_OF_BIRTH: profile.date_of_birth.isoformat(),
            CONF_HEIGHT_CM: profile.height_cm,
            CONF_ATHLETE_MODE: profile.athlete_mode,
            CONF_REFERENCE_STANDARD: profile.reference_standard.value,
        }

    return vol.Schema(
        {
            vol.Required(
                CONF_PROFILE_NAME,
                default=defaults.get(CONF_PROFILE_NAME, vol.UNDEFINED),
            ): vol.All(str, vol.Strip, vol.Length(min=1, max=64)),
            vol.Required(
                CONF_PROFILE_PIN,
                default=defaults.get(CONF_PROFILE_PIN, vol.UNDEFINED),
            ): vol.All(str, vol.Match(r"^\d{4}$")),
            vol.Required(
                CONF_SEX,
                default=defaults.get(CONF_SEX, vol.UNDEFINED),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=["female", "male"],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="sex",
                )
            ),
            vol.Required(
                CONF_DATE_OF_BIRTH,
                default=defaults.get(CONF_DATE_OF_BIRTH, vol.UNDEFINED),
            ): DateSelector(),
            vol.Required(
                CONF_HEIGHT_CM,
                default=defaults.get(CONF_HEIGHT_CM, vol.UNDEFINED),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=300,
                    step=0.1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="cm",
                )
            ),
            vol.Required(
                CONF_ATHLETE_MODE,
                default=defaults.get(CONF_ATHLETE_MODE, False),
            ): bool,
            vol.Required(
                CONF_REFERENCE_STANDARD,
                default=defaults.get(
                    CONF_REFERENCE_STANDARD, ReferenceStandard.EXTERNAL.value
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        ReferenceStandard.EXTERNAL.value,
                        ReferenceStandard.INTERNAL.value,
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="reference_standard",
                )
            ),
        }
    )


def _profile_from_user_input(
    user_input: dict[str, Any], *, profile_id: str | None = None
) -> UserProfile:
    """Convert and validate one options-flow form result."""
    raw_birth_date = user_input[CONF_DATE_OF_BIRTH]
    birth_date = (
        raw_birth_date
        if isinstance(raw_birth_date, date)
        else date.fromisoformat(raw_birth_date)
    )
    current_age = age_on(birth_date, date.today())
    if not 1 <= current_age <= 120:
        raise ValueError("profile age must currently be in [1, 120]")

    return UserProfile(
        name=user_input[CONF_PROFILE_NAME],
        profile_pin=user_input[CONF_PROFILE_PIN],
        profile_id=uuid4().hex if profile_id is None else profile_id,
        sex=Sex[user_input[CONF_SEX].upper()],
        date_of_birth=birth_date,
        height_cm=user_input[CONF_HEIGHT_CM],
        athlete_mode=user_input[CONF_ATHLETE_MODE],
        reference_standard=ReferenceStandard(user_input[CONF_REFERENCE_STANDARD]),
    )


def _profile_choice_schema(profiles: dict[str, UserProfile]) -> vol.Schema:
    """Build a stable profile picker for edit/delete steps."""
    return vol.Schema(
        {
            vol.Required(CONF_PROFILE_PIN): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=profile_pin,
                            label=f"{profiles[profile_pin].name} ({profile_pin})",
                        )
                        for profile_pin in sorted(profiles)
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


class GarlynScaleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a config entry for one physical GARLYN scale."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> GarlynScaleOptionsFlow:
        """Create the profile options flow."""
        del config_entry
        return GarlynScaleOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial setup."""
        errors: dict[str, str] = {}
        suggested_name = DEFAULT_SCALE_NAME

        if user_input is not None:
            suggested_name = user_input[CONF_SCALE_NAME]
            scale_name = suggested_name.strip()
            if not scale_name:
                errors[CONF_SCALE_NAME] = "invalid_name"
            else:
                scale_id = uuid4().hex
                webhook_id = webhook.async_generate_id()
                await self.async_set_unique_id(scale_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=scale_name,
                    data={
                        CONF_SCALE_ID: scale_id,
                        CONF_WEBHOOK_ID: webhook_id,
                    },
                    description_placeholders={
                        "scale_id": scale_id,
                        "webhook_path": webhook.async_generate_path(webhook_id),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    # A label is required because multiple scales are valid.
                    vol.Required(CONF_SCALE_NAME, default=suggested_name): vol.All(
                        str, vol.Length(min=1, max=64)
                    )
                }
            ),
            errors=errors,
        )


class GarlynScaleOptionsFlow(OptionsFlowWithReload):
    """Add, edit, and remove profiles stored in mutable entry options."""

    _selected_profile_pin: str | None = None

    def _profiles(self) -> dict[str, UserProfile]:
        return deserialize_profiles(self.config_entry.options)

    def _save_profiles(self, profiles: dict[str, UserProfile]) -> ConfigFlowResult:
        options = dict(self.config_entry.options)
        options[CONF_PROFILES] = serialize_profiles(profiles)
        return self.async_create_entry(data=options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show profile-management actions."""
        del user_input
        profiles = self._profiles()
        menu_options = ["add_profile"]
        if profiles:
            menu_options.extend(("edit_profile", "delete_profile"))
        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
            description_placeholders={"profile_count": str(len(profiles))},
        )

    async def async_step_add_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a profile with a unique four-digit PIN."""
        errors: dict[str, str] = {}
        profiles = self._profiles()
        if user_input is not None:
            try:
                profile = _profile_from_user_input(user_input)
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_profile"
            else:
                if profile.profile_pin in profiles:
                    errors[CONF_PROFILE_PIN] = "pin_already_configured"
                else:
                    profiles[profile.profile_pin] = profile
                    return self._save_profiles(profiles)

        return self.async_show_form(
            step_id="add_profile",
            data_schema=_profile_form_schema(),
            errors=errors,
        )

    async def async_step_edit_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the profile to edit."""
        profiles = self._profiles()
        if not profiles:
            return await self.async_step_add_profile()
        if user_input is not None:
            self._selected_profile_pin = user_input[CONF_PROFILE_PIN]
            return await self.async_step_edit_profile_details()
        return self.async_show_form(
            step_id="edit_profile",
            data_schema=_profile_choice_schema(profiles),
        )

    async def async_step_edit_profile_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the selected profile, including a safe PIN change."""
        profiles = self._profiles()
        selected_pin = self._selected_profile_pin
        if selected_pin is None or selected_pin not in profiles:
            return await self.async_step_init()

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                profile = _profile_from_user_input(
                    user_input,
                    profile_id=profiles[selected_pin].profile_id,
                )
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_profile"
            else:
                pin_conflicts = (
                    profile.profile_pin != selected_pin
                    and profile.profile_pin in profiles
                )
                if pin_conflicts:
                    errors[CONF_PROFILE_PIN] = "pin_already_configured"
                else:
                    del profiles[selected_pin]
                    profiles[profile.profile_pin] = profile
                    return self._save_profiles(profiles)

        return self.async_show_form(
            step_id="edit_profile_details",
            data_schema=_profile_form_schema(profiles[selected_pin]),
            errors=errors,
        )

    async def async_step_delete_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the profile to remove."""
        profiles = self._profiles()
        if not profiles:
            return await self.async_step_add_profile()
        if user_input is not None:
            self._selected_profile_pin = user_input[CONF_PROFILE_PIN]
            return await self.async_step_confirm_delete_profile()
        return self.async_show_form(
            step_id="delete_profile",
            data_schema=_profile_choice_schema(profiles),
        )

    async def async_step_confirm_delete_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm removal of the selected profile."""
        profiles = self._profiles()
        selected_pin = self._selected_profile_pin
        if selected_pin is None or selected_pin not in profiles:
            return await self.async_step_init()
        profile = profiles[selected_pin]
        if user_input is not None:
            del profiles[selected_pin]
            return self._save_profiles(profiles)
        return self.async_show_form(
            step_id="confirm_delete_profile",
            data_schema=vol.Schema({}),
            description_placeholders={
                "profile_name": profile.name,
                "profile_pin": profile.profile_pin,
            },
        )
