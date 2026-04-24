"""Tests for the Comwatt config flow."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

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
