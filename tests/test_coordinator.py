"""Coordinator tests: back-off and pausing after an unsupported sign-in page."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.mijnted.api import (
    MijntedConnectionError,
    MijntedUnexpectedPageError,
    SignInStep,
)
from custom_components.mijnted.const import MAX_BACKOFF

from .helpers import at, refresh, setup_entry

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .fake_portal import FakePortal

pytestmark = pytest.mark.usefixtures("amsterdam")


@pytest.mark.usefixtures("mock_client")
async def test_unexpected_page_later_stops_polling(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test unexpected page later stops polling."""
    at(freezer, "2026-11-14 12:00:00")
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    portal.fail_with = MijntedUnexpectedPageError(SignInStep.AUTHORIZE)
    await refresh(hass, config_entry)
    assert coordinator.update_interval is None
    assert not coordinator.last_update_success

    portal.fail_with = None
    updates = portal.updates
    freezer.tick(timedelta(days=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert portal.updates == updates


@pytest.mark.usefixtures("mock_client")
async def test_transient_errors_back_off(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test transient errors back off."""
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


@pytest.mark.usefixtures("mock_client")
async def test_back_off_after_many_failures(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test a month of failures keeps backing off at the cap without overflowing."""
    at(freezer, "2026-11-14 12:00:00")
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    portal.fail_with = MijntedConnectionError("ClientConnectorError")
    for _ in range(40):
        await refresh(hass, config_entry)
    assert coordinator.update_interval == MAX_BACKOFF
    # A clean UpdateFailed, not an unexpected error from the interval calculation.
    assert isinstance(coordinator.last_exception, UpdateFailed)


@pytest.mark.usefixtures("mock_client")
async def test_incomplete_day_is_fetched_again(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test a past day that came back without one meter's value is not kept."""
    at(freezer, "2026-11-15 10:00:00")
    first, second = portal.devices[:2]
    for offset, value in enumerate([10, 12, 17, 22]):
        day = date(2026, 11, 10) + timedelta(days=offset)
        portal.set_day(day, value, first)
        portal.set_day(day, value // 2, second)
    portal.last_sync = date(2026, 11, 13)
    portal.missing = {(date(2026, 11, 11), second)}
    await setup_entry(hass, config_entry)
    meter = config_entry.runtime_data.data.meters[second]
    assert date(2026, 11, 11) not in meter.days

    portal.missing = set()
    portal.fetched_days.clear()
    await refresh(hass, config_entry)
    meter = config_entry.runtime_data.data.meters[second]
    assert meter.days[date(2026, 11, 11)] == 6
    assert sorted(portal.fetched_days) == [date(2026, 11, 11), date(2026, 11, 13)]
