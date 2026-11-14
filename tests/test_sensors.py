"""Sensor tests: lagging data, weekend backfill, season reset, repeated readouts."""

from __future__ import annotations

from datetime import date

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .fake_portal import FakePortal
from .helpers import at, fill, refresh, setup_entry, state

pytestmark = pytest.mark.usefixtures("amsterdam")


@pytest.mark.usefixtures("mock_client")
async def test_lagging_data_uses_last_readout(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """On Sunday the portal is read out up to Friday; Saturday repeats Friday and is skipped."""
    at(freezer, "2026-11-15 10:00:00")
    fill(portal, date(2026, 11, 10), [10, 12, 17, 22])
    portal.last_sync = date(2026, 11, 13)

    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    device = portal.devices[0]
    total = state(hass, device, "cumulative")
    assert float(total.state) == 22
    assert total.attributes["last_readout_date"] == "2026-11-13"
    assert total.attributes["statistic_id"] == f"mijnted:meter_{device}"
    readout = state(hass, device, "last_readout")
    assert float(readout.state) == 5
    assert readout.attributes["readout_date"] == "2026-11-13"
    assert max(portal.fetched_days) == date(2026, 11, 13)
    assert min(portal.fetched_days) == date(2026, 10, 16)


@pytest.mark.usefixtures("mock_client")
async def test_weekend_backfill_on_monday(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Saturday and Sunday arrive on Monday with their own values; old days are not refetched."""
    at(freezer, "2026-11-14 12:00:00")
    fill(portal, date(2026, 11, 10), [10, 12, 17, 22])
    portal.last_sync = date(2026, 11, 13)
    await setup_entry(hass, config_entry)
    meter = config_entry.runtime_data.data.meters[portal.devices[0]]
    assert max(meter.days) == date(2026, 11, 13)

    at(freezer, "2026-11-16 18:00:00")
    fill(portal, date(2026, 11, 14), [26, 31, 33])
    portal.last_sync = date(2026, 11, 16)
    portal.fetched_days.clear()
    await refresh(hass, config_entry)

    meter = config_entry.runtime_data.data.meters[portal.devices[0]]
    assert meter.days[date(2026, 11, 14)] == 26
    assert meter.days[date(2026, 11, 15)] == 31
    assert meter.days[date(2026, 11, 16)] == 33
    # Days before Friday's readout were final and cached; Friday itself is fetched again.
    assert sorted(portal.fetched_days) == [date(2026, 11, d) for d in (13, 14, 15, 16)]
    assert float(state(hass, portal.devices[0], "last_readout").state) == 2


@pytest.mark.usefixtures("mock_client")
async def test_season_reset(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """The counter restarts at zero; the readout counts the new value, not a negative change."""
    at(freezer, "2026-06-01 09:00:00")
    fill(portal, date(2026, 5, 28), [487, 489, 489, 3])
    portal.last_sync = date(2026, 5, 31)
    await setup_entry(hass, config_entry)

    device = portal.devices[0]
    assert float(state(hass, device, "cumulative").state) == 3
    assert float(state(hass, device, "last_readout").state) == 3


@pytest.mark.usefixtures("mock_client")
async def test_repeated_last_readout(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """A readout day without heating keeps its own date and reports zero units."""
    at(freezer, "2026-11-14 12:00:00")
    fill(portal, date(2026, 11, 11), [12, 17, 17])
    portal.last_sync = date(2026, 11, 13)
    await setup_entry(hass, config_entry)

    readout = state(hass, portal.devices[0], "last_readout")
    assert float(readout.state) == 0
    assert readout.attributes["readout_date"] == "2026-11-13"


@pytest.mark.usefixtures("mock_client")
async def test_no_readout_yet(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    at(freezer, "2026-11-14 12:00:00")
    portal.last_sync = None
    await setup_entry(hass, config_entry)
    assert state(hass, portal.devices[0], "cumulative").state == "unknown"
    assert portal.fetched_days == []
