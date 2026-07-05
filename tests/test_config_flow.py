"""Tests for the Comwatt config flow."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN

from .conftest import _FakeCookie

USER_INPUT = {"username": "user@example.com", "password": "secret"}


async def test_form_is_shown_first(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """The user step shows a form with no errors on first entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_happy_path_creates_entry(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Valid credentials create a config entry titled with the username."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == USER_INPUT["username"]
    assert result2["data"] == USER_INPUT
    mock_comwatt_client.authenticate.assert_any_call(
        USER_INPUT["username"], USER_INPUT["password"]
    )


async def test_invalid_auth_when_session_cookie_missing(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """If authenticate() succeeds but no cwt_session cookie is set, show invalid_auth."""
    mock_comwatt_client.session.cookies = [_FakeCookie("other", "x")]

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect_when_authenticate_raises(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """If authenticate() raises, show cannot_connect."""
    mock_comwatt_client.authenticate.side_effect = RuntimeError("boom")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize(
    "missing_field",
    ["username", "password"],
)
async def test_schema_requires_credentials(
    hass: HomeAssistant,
    mock_comwatt_client: MagicMock,
    missing_field: str,
) -> None:
    """Submitting the form without username/password is rejected by the schema."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    partial = {k: v for k, v in USER_INPUT.items() if k != missing_field}

    import voluptuous as vol

    with pytest.raises(vol.Invalid):
        await hass.config_entries.flow.async_configure(result["flow_id"], partial)


# ----------------------------------------------------------------------
# Reauth flow (finding C7)
# ----------------------------------------------------------------------


def _add_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, title=USER_INPUT["username"])
    entry.add_to_hass(hass)
    return entry


async def test_reauth_prompts_for_new_password(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Starting a reauth flow shows the reauth_confirm form, prefilling the
    username in the description placeholder."""
    entry = _add_entry(hass)

    result = await entry.start_reauth_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {}
    assert result["description_placeholders"]["username"] == USER_INPUT["username"]


async def test_reauth_happy_path_updates_password(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A valid new password replaces the entry's password and aborts with
    `reauth_successful`."""
    entry = _add_entry(hass)
    init = await entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        init["flow_id"], {"password": "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {**USER_INPUT, "password": "new-password"}
    mock_comwatt_client.authenticate.assert_any_call(
        USER_INPUT["username"], "new-password"
    )


async def test_reauth_invalid_auth_keeps_form_open(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """If the new password is rejected by the backend, the form stays open
    with `invalid_auth` and the entry's data is not touched."""
    # Simulate the same invalid-auth condition as the initial flow: a
    # successful authenticate() but no cwt_session cookie.
    mock_comwatt_client.session.cookies = [_FakeCookie("other", "x")]

    entry = _add_entry(hass)
    init = await entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        init["flow_id"], {"password": "still-wrong"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data == USER_INPUT  # unchanged


async def test_reauth_cannot_connect_keeps_form_open(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A transient backend error shows `cannot_connect` without losing the
    entry's current credentials."""
    mock_comwatt_client.authenticate.side_effect = RuntimeError("boom")

    entry = _add_entry(hass)
    init = await entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        init["flow_id"], {"password": "anything"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data == USER_INPUT
