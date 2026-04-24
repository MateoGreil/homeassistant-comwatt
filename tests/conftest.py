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


class _FakeCookie:
    """Minimal stand-in for a requests cookie used by the config flow."""

    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class _FakeCookieJar(list):
    """A list of cookies that mimics the subset of `RequestsCookieJar` the
    integration uses: iteration, `get_dict()`, and `update()`."""

    def get_dict(self) -> dict[str, str]:
        return {c.name: c.value for c in self}

    def update(self, other: object) -> None:  # type: ignore[override]
        if isinstance(other, dict):
            for name, value in other.items():
                self.append(_FakeCookie(name, value))
            return
        for cookie in other:  # type: ignore[union-attr]
            self.append(cookie)


def _make_fake_client() -> MagicMock:
    """Build a ComwattClient mock with sensible defaults."""
    instance = MagicMock()
    instance.session.cookies = _FakeCookieJar([_FakeCookie("cwt_session", "fake")])
    instance.get_sites.return_value = []
    instance.get_devices.return_value = []
    instance.authenticate.return_value = None
    return instance


@pytest.fixture
def mock_comwatt_client() -> Generator[MagicMock, None, None]:
    """Patch ComwattClient wherever the integration imports it."""
    instance = _make_fake_client()
    with patch(
        "custom_components.comwatt.ComwattClient", return_value=instance
    ), patch(
        "custom_components.comwatt.config_flow.ComwattClient", return_value=instance
    ), patch(
        "custom_components.comwatt.sensor.ComwattClient", return_value=instance
    ), patch(
        "custom_components.comwatt.switch.ComwattClient", return_value=instance
    ):
        yield instance
