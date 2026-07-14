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
    instance.stream_measurements.return_value = iter([])
    return instance


@pytest.fixture
def mock_comwatt_client() -> Generator[MagicMock, None, None]:
    """Patch ComwattClient at every site the integration imports it.

    The coordinator owns the long-lived polling client and the config flow
    creates a one-shot client for credential validation; both share `instance`
    so tests assert on a single mock. The stream manager owns a *dedicated*
    client (mirroring production, where it never shares the coordinator's
    `requests.Session`), so it gets its own `stream_instance` whose
    `stream_measurements` returns an empty iterator and whose `authenticate`
    is a no-op. This keeps the coordinator's `authenticate.call_count` free
    of the stream's own authentication.
    """
    instance = _make_fake_client()
    stream_instance = _make_fake_client()
    with patch(
        "custom_components.comwatt.coordinator.ComwattClient", return_value=instance
    ), patch(
        "custom_components.comwatt.config_flow.ComwattClient", return_value=instance
    ), patch(
        "custom_components.comwatt.stream.ComwattClient", return_value=stream_instance
    ):
        yield instance
