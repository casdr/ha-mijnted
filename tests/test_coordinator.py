"""Coordinator and sensor tests: lagging data, weekend backfill, season reset, failures."""

from __future__ import annotations

from datetime import date, timedelta
import json
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mijnted.api import (
    MijntedAuthError,
    MijntedConnectionError,
    MijntedUnexpectedPageError,
)
from custom_components.mijnted.const import DOMAIN
from custom_components.mijnted.diagnostics import async_get_config_entry_diagnostics

from .conftest import ACCOUNT_ID, EMAIL, PASSWORD, FakePortal

pytestmark = pytest.mark.usefixtures("amsterdam")


def at(freezer: FrozenDateTimeFactory, moment: str) -> None:
    """Move the clock to a local Amsterdam time."""
    freezer.move_to(f"{moment}+01:00")


def fill(portal: FakePortal, first: date, values: list[float]) -> None:
    for offset, value in enumerate(values):
        portal.set_day(first + timedelta(days=offset), value)


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def state(hass: HomeAssistant, device: str, key: str) -> Any:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{device}_{key}")
    assert entity_id
    return hass.states.get(entity_id)


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


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


@pytest.mark.usefixtures("mock_client")
async def test_bad_credentials_start_reauth(
    hass: HomeAssistant, portal: FakePortal, config_entry: MockConfigEntry
) -> None:
    portal.fail_with = MijntedAuthError("rejected")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


@pytest.mark.usefixtures("mock_client")
async def test_unexpected_page_at_setup(
    hass: HomeAssistant, portal: FakePortal, config_entry: MockConfigEntry
) -> None:
    """An MFA or captcha page fails setup without retrying and raises a repair issue."""
    portal.fail_with = MijntedUnexpectedPageError("confirm")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"unexpected_sign_in_page_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {"step": "confirm", "title": EMAIL}


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


@pytest.mark.usefixtures("mock_client")
async def test_export_disabled_raises_issue(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    at(freezer, "2026-11-14 12:00:00")
    portal.export_active = False
    await setup_entry(hass, config_entry)
    assert config_entry.runtime_data.data.meters == {}
    assert ir.async_get(hass).async_get_issue(DOMAIN, "export_disabled_100001") is not None


@pytest.mark.usefixtures("mock_client")
async def test_devices_and_names(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Meters are named after their room plus number and linked to the apartment."""
    at(freezer, "2026-11-14 12:00:00")
    await setup_entry(hass, config_entry)
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, config_entry.entry_id)
    hub = next(device for device in devices if (DOMAIN, "unit_100001") in device.identifiers)
    meters = [device for device in devices if device.via_device_id == hub.id]
    assert len(meters) == 6
    names = sorted(device.name for device in meters)
    assert names[0] == "Kitchen 90000004"
    assert "Living room 90000001" in names
    assert hub.model == "F71"


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
