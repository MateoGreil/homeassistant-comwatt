"""Config flow for Comwatt integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from comwatt_client import ComwattClient

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)

# The reauth step only asks for a new password — the username is the one the
# user already entered when the entry was created; we copy it in for them.
STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required("password"): str})


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Try to authenticate; raise on failure.

    Returns `None` on success. Raises `InvalidAuth` for bad credentials,
    `CannotConnect` for network / backend failures.
    """
    cwt_session = None
    client = ComwattClient()
    try:
        await asyncio.to_thread(
            lambda: client.authenticate(data["username"], data["password"])
        )
        for cookie in client.session.cookies:
            if cookie.name == "cwt_session":
                cwt_session = cookie
                break
    except Exception as err:  # noqa: BLE001 - upstream raises bare Exception
        raise CannotConnect from err

    if not cwt_session:
        raise InvalidAuth


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Comwatt."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input["username"], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Triggered when the coordinator raises `ConfigEntryAuthFailed`.

        Forwards straight to the confirmation step where we ask the user for
        a fresh password.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prompt the user for a new password and update the entry."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            new_data = {
                "username": entry.data["username"],
                "password": user_input["password"],
            }
            try:
                await validate_input(self.hass, new_data)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"
            else:
                # Writes the new data to the entry, reloads it, and ends the
                # flow with the `reauth_successful` abort reason.
                return self.async_update_reload_and_abort(entry, data=new_data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={"username": entry.data["username"]},
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
