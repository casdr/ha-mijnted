"""Coordinator tests: back-off and pausing after an unsupported sign-in page."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mijnted.api import (
    MijntedConnectionError,
    MijntedUnexpectedPageError,
)

from .fake_portal import FakePortal
from .helpers import at, refresh, setup_entry

pytestmark = pytest.mark.usefixtures("amsterdam")


@pytest.mark.usefixtures("mock_client")
async def test_unexpected_page_later_stops_polling(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    at(freezer, "2026-11-14 12:00:00")
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    portal.fail_with = MijntedUnexpectedPageError("authorize")
    await refresh(hass, config_entry)
    assert coordinator.update_interval is None
    assert not coordinator.last_update_success

    portal.fail_with = None
    sign_ins = portal.sign_ins
    freezer.tick(timedelta(days=2))
    await hass.async_block_till_done()
    assert portal.sign_ins == sign_ins


@pytest.mark.usefixtures("mock_client")
async def test_transient_errors_back_off(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    at(freezer, "2026-11-14 12:00:00")
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval == timedelta(hours=6)

    portal.fail_with = MijntedConnectionError("ServerTimeoutError")
    await refresh(hass, config_entry)
    assert coordinator.update_interval == timedelta(hours=12)
    await refresh(hass, config_entry)
    assert coordinator.update_interval == timedelta(hours=24)
    await refresh(hass, config_entry)
    assert coordinator.update_interval == timedelta(hours=24)

    portal.fail_with = None
    await refresh(hass, config_entry)
    assert coordinator.update_interval == timedelta(hours=6)
    assert coordinator.last_update_success
