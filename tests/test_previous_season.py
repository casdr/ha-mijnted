"""Previous-season comparison: statistic, sensors, storage and the season rollover."""

from __future__ import annotations

from datetime import date, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.mijnted.api import MijntedConnectionError
from custom_components.mijnted.const import DOMAIN
from custom_components.mijnted.seasons import PreviousSeason
from custom_components.mijnted.statistics import async_import_previous_season

from .helpers import at, refresh, setup_entry, state

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .fake_portal import FakePortal

pytestmark = pytest.mark.usefixtures("amsterdam")

STAT_ID = "mijnted:total_previous_season"
CURRENT_ID = "mijnted:total"


def ramp(first: date, last: date, final: float) -> dict[date, float]:
    """Count up evenly from 0 on `first` to `final` on `last`."""
    days = (last - first).days
    return {
        first + timedelta(days=offset): round(final * offset / days)
        for offset in range(days + 1)
    }


def _query(hass: HomeAssistant, stat_id: str) -> dict[date, dict[str, Any]]:
    result = statistics_during_period(
        hass,
        dt_util.start_of_local_day(date(2024, 1, 1)),
        None,
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return {
        dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date(): dict(row)
        for row in result.get(stat_id, [])
    }


async def stat_rows(
    hass: HomeAssistant, stat_id: str = STAT_ID
) -> dict[date, dict[str, Any]]:
    """Return a statistic's rows by local day, the previous season's by default."""
    await async_wait_recording_done(hass)
    return await hass.async_add_executor_job(partial(_query, hass, stat_id))


def season(first: date, last: date, final: float) -> PreviousSeason:
    """Return a season counting up to `final`."""
    return PreviousSeason(start=first, end=last, days=ramp(first, last, final))


async def test_statistic_is_shifted_one_year(hass: HomeAssistant) -> None:
    """Test 15 Nov 2026 shows 15 Nov 2025, and future days are imported too."""
    previous = season(date(2025, 6, 2), date(2026, 6, 1), 489)
    await async_import_previous_season(hass, STAT_ID, previous)
    rows = await stat_rows(hass)

    assert rows[date(2026, 11, 15)]["state"] == previous.days[date(2025, 11, 15)]
    assert min(rows) == date(2026, 6, 2)
    assert max(rows) == date(2027, 6, 1)
    assert rows[date(2027, 6, 1)]["state"] == 489


async def test_statistic_import_is_idempotent(hass: HomeAssistant) -> None:
    """Test importing the same season again leaves the rows as they were."""
    previous = season(date(2025, 6, 2), date(2026, 6, 1), 489)
    await async_import_previous_season(hass, STAT_ID, previous)
    first = await stat_rows(hass)
    await async_import_previous_season(hass, STAT_ID, previous)
    await async_import_previous_season(hass, STAT_ID, previous)
    assert await stat_rows(hass) == first


async def test_back_to_back_imports_continue_the_sum(hass: HomeAssistant) -> None:
    """Test a second import right after the first, before the recorder wrote it."""
    first = season(date(2024, 6, 2), date(2025, 6, 1), 400)
    second = season(date(2025, 6, 2), date(2026, 6, 1), 489)
    await async_import_previous_season(hass, STAT_ID, first)
    await async_import_previous_season(hass, STAT_ID, second)
    rows = await stat_rows(hass)
    assert rows[date(2027, 6, 1)]["sum"] == 400 + 489


async def test_statistic_rollover_keeps_the_sum_going(hass: HomeAssistant) -> None:
    """Test the next previous season continues the sum without a negative day."""
    await async_import_previous_season(
        hass, STAT_ID, season(date(2024, 6, 2), date(2025, 6, 1), 400)
    )
    await async_import_previous_season(
        hass, STAT_ID, season(date(2025, 6, 2), date(2026, 6, 1), 489)
    )
    rows = await stat_rows(hass)
    sums = [rows[day]["sum"] for day in sorted(rows)]
    assert sums == sorted(sums)
    assert rows[date(2026, 6, 1)]["state"] == 400
    assert rows[date(2026, 6, 2)]["state"] == 0
    assert rows[date(2027, 6, 1)]["sum"] == 400 + 489


def unit_state(hass: HomeAssistant, key: str) -> Any:
    """State of a residential unit's sensor."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"unit_100001_{key}"
    )
    assert entity_id
    return hass.states.get(entity_id)


async def setup_with_history(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Set up on 15 Nov 2026 with three seasons of data, and let the backfill run."""
    at(freezer, "2026-11-15 10:00:00")
    portal.fill_ramp(date(2024, 6, 2), date(2025, 6, 1), 400)
    portal.fill_ramp(date(2025, 6, 2), date(2026, 6, 1), 489)
    portal.fill_ramp(date(2026, 6, 2), date(2026, 11, 13), 22)
    portal.last_sync = date(2026, 11, 13)
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.usefixtures("mock_client")
async def test_sensors_after_backfill(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test the unit's sensors compare with the same date in the previous season."""
    await setup_with_history(hass, freezer, portal, config_entry)
    expected = ramp(date(2025, 6, 2), date(2026, 6, 1), 489)

    to_date = unit_state(hass, "previous_season_to_date")
    assert float(to_date.state) == expected[date(2025, 11, 15)]
    assert to_date.attributes["comparison_date"] == "2025-11-15"
    assert to_date.attributes["season_start"] == "2025-06-02"
    assert to_date.attributes["state_class"] == "total"
    assert to_date.attributes["last_reset"].startswith("2026-06-02T00:00:00")
    total = unit_state(hass, "previous_season_total")
    assert float(total.state) == 489
    assert "state_class" not in total.attributes

    rows = await stat_rows(hass)
    assert rows[date(2026, 11, 15)]["state"] == expected[date(2025, 11, 15)]

    # This season's household total from its first day, next to last season's.
    current = await stat_rows(hass, CURRENT_ID)
    assert min(current) == date(2026, 6, 2)
    assert max(current) == date(2026, 11, 13)
    assert current[date(2026, 11, 13)]["state"] == 22
    sums = [current[day]["sum"] for day in sorted(current)]
    assert sums == sorted(sums)
    assert sums[-1] == 22

    # At midnight the to-date value moves on a day without a portal update.
    at(freezer, "2026-11-16 00:00:06")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    to_date = unit_state(hass, "previous_season_to_date")
    assert float(to_date.state) == expected[date(2025, 11, 16)]
    meter = state(hass, portal.devices[0], "previous_season_to_date")
    assert float(meter.state) == expected[date(2025, 11, 16)]


@pytest.mark.usefixtures("mock_client")
async def test_previous_season_per_meter(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test each meter gets its own shifted statistic and to-date sensor."""
    at(freezer, "2026-11-15 10:00:00")
    first, second = portal.devices[:2]
    for device, final in ((first, 300), (second, 189)):
        for day, value in ramp(date(2025, 6, 2), date(2026, 6, 1), final).items():
            portal.set_day(day, value, device)
        for day, value in ramp(date(2026, 6, 2), date(2026, 11, 13), 11).items():
            portal.set_day(day, value, device)
    portal.last_sync = date(2026, 11, 13)
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)

    expected = ramp(date(2025, 6, 2), date(2026, 6, 1), 189)
    sensor = state(hass, second, "previous_season_to_date")
    assert float(sensor.state) == expected[date(2025, 11, 15)]
    assert sensor.attributes["statistic_id"] == (
        f"mijnted:meter_{second}_previous_season"
    )
    rows = await stat_rows(hass, f"mijnted:meter_{second}_previous_season")
    assert rows[date(2026, 11, 15)]["state"] == expected[date(2025, 11, 15)]
    assert rows[date(2027, 6, 1)]["state"] == 189
    assert float(unit_state(hass, "previous_season_total").state) == 300 + 189
    # A meter without values last season has none to compare with.
    assert state(hass, portal.devices[2], "previous_season_to_date").state == (
        "unknown"
    )


@pytest.mark.usefixtures("mock_client")
async def test_season_stored_without_meters_is_fetched_again(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Test a season stored by 0.2.0 is fetched once more for the meter values."""
    previous = season(date(2025, 6, 2), date(2026, 6, 1), 489).as_dict()
    del previous["meters"]
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}.seasons"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}.seasons",
        "data": {
            "units": {"100001": {"current_start": "2026-06-02", "previous": previous}}
        },
    }
    await setup_with_history(hass, freezer, portal, config_entry)

    # Fetched during setup, without waiting for a coordinator update.
    assert portal.backfill_days
    meter = state(hass, portal.devices[0], "previous_season_to_date")
    assert meter.state != "unknown"
    rows = await stat_rows(hass, f"mijnted:meter_{portal.devices[0]}_previous_season")
    assert rows[date(2027, 6, 1)]["state"] == 489
    stored = hass_storage[f"{DOMAIN}.{config_entry.entry_id}.seasons"]["data"]
    assert portal.devices[0] in stored["units"]["100001"]["previous"]["meters"]


@pytest.mark.usefixtures("mock_client")
async def test_backfill_is_stored_and_not_repeated(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test a restart takes the previous season from storage."""
    await setup_with_history(hass, freezer, portal, config_entry)
    assert len(portal.backfill_days) > 365

    portal.backfill_days.clear()
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert portal.backfill_days == []
    assert float(unit_state(hass, "previous_season_total").state) == 489


@pytest.mark.usefixtures("mock_client")
async def test_new_season_becomes_the_previous_one(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test the season that just ended becomes the previous season after a reset."""
    await setup_with_history(hass, freezer, portal, config_entry)

    at(freezer, "2027-06-10 10:00:00")
    portal.fill_ramp(date(2026, 6, 2), date(2027, 6, 1), 300)
    portal.fill_ramp(date(2027, 6, 2), date(2027, 6, 9), 2)
    portal.last_sync = date(2027, 6, 9)
    portal.backfill_days.clear()
    await refresh(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert portal.backfill_days
    total = unit_state(hass, "previous_season_total")
    assert float(total.state) == 300
    assert total.attributes["season_start"] == "2026-06-02"
    assert total.attributes["season_end"] == "2027-06-01"
    rows = await stat_rows(hass)
    assert (
        rows[date(2027, 6, 10)]["state"]
        == ramp(date(2026, 6, 2), date(2027, 6, 1), 300)[date(2026, 6, 10)]
    )
    sums = [rows[day]["sum"] for day in sorted(rows)]
    assert sums == sorted(sums)


@pytest.mark.usefixtures("mock_client")
async def test_dips_after_the_start_do_not_fetch_again(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test a reset over two days, and a dip early in the season, fetch only once."""
    at(freezer, "2026-06-20 10:00:00")
    portal.fill_ramp(date(2024, 6, 2), date(2025, 6, 1), 400)
    portal.fill_ramp(date(2025, 6, 2), date(2026, 5, 31), 489)
    portal.set_day(date(2026, 6, 1), 200)
    portal.fill_ramp(date(2026, 6, 2), date(2026, 6, 19), 6)
    portal.set_day(date(2026, 6, 12), 1)  # 2 -> 1 -> 2: a dip to half
    portal.last_sync = date(2026, 6, 19)
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert float(unit_state(hass, "previous_season_total").state) == 489

    portal.backfill_days.clear()
    await refresh(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert portal.backfill_days == []


@pytest.mark.usefixtures("mock_client")
async def test_backfill_survives_a_failed_request(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test a timeout among the backfill's hundreds of requests is retried."""
    portal.backfill_errors = [
        MijntedConnectionError("ServerTimeoutError"),
        MijntedConnectionError("ServerTimeoutError"),
    ]
    await setup_with_history(hass, freezer, portal, config_entry)
    assert not portal.backfill_errors
    assert float(unit_state(hass, "previous_season_total").state) == 489
