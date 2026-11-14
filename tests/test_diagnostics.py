"""Diagnostics tests."""

from __future__ import annotations

import json
from datetime import date

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mijnted.diagnostics import async_get_config_entry_diagnostics

from .fake_portal import ACCOUNT_ID, EMAIL, PASSWORD, FakePortal
from .helpers import at, fill, setup_entry

pytestmark = pytest.mark.usefixtures("amsterdam")


@pytest.mark.usefixtures("mock_client")
async def test_diagnostics_are_redacted(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    at(freezer, "2026-11-14 12:00:00")
    fill(portal, date(2026, 11, 12), [17, 22])
    portal.last_sync = date(2026, 11, 13)
    await setup_entry(hass, config_entry)

    result = await async_get_config_entry_diagnostics(hass, config_entry)
    dumped = json.dumps(result)
    for secret in (EMAIL, PASSWORD, ACCOUNT_ID, "100001", "2001", *portal.devices):
        assert secret not in dumped
    assert result["meters"][0]["days"]["2026-11-13"] == 22
