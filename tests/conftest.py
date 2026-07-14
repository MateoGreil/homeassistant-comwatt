"""Shared fixtures for Comwatt tests."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None, None, None]:
    """Enable loading custom integrations in every test."""
    yield


def _make_fake_client() -> MagicMock:
    """Build a ComwattClient mock with sensible defaults."""
    instance = MagicMock()
    instance.get_sites.return_value = []
    instance.get_devices.return_value = []
    instance.get_connected_objects.return_value = []
    instance.authenticate.return_value = None
    return instance


@pytest.fixture
def mock_comwatt_client() -> Generator[MagicMock, None, None]:
    """Patch ComwattClient at the two sites the integration imports it.

    The coordinator owns the long-lived client; the config flow still creates
    a one-shot client for credential validation.
    """
    instance = _make_fake_client()
    with patch(
        "custom_components.comwatt.coordinator.ComwattClient", return_value=instance
    ), patch(
        "custom_components.comwatt.config_flow.ComwattClient", return_value=instance
    ):
        yield instance
