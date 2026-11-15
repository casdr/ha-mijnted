"""Household total statistic and statistic names."""

from __future__ import annotations

from datetime import date, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.mijnted.const import DOMAIN

from .helpers import at, refresh, setup_entry

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .fake_portal import FakePortal

pytestmark = pytest.mark.usefixtures("amsterdam", "mock_client")

TOTAL_ID = "mijnted:total"
FIRST = date(2026, 11, 9)


def _query(hass: HomeAssistant, stat_id: str) -> dict[date, dict[str, Any]]:
    result = statistics_during_period(
        hass,
        dt_util.start_of_local_day(date(2026, 1, 1)),
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


async def stat_rows(hass: HomeAssistant, stat_id: str) -> dict[date, dict[str, Any]]:
    """Return a statistic's rows by local day."""
    await async_wait_recording_done(hass)
    return await hass.async_add_executor_job(partial(_query, hass, stat_id))


def three_meters(portal: FakePortal) -> list[str]:
    """Give three meters values from 9 to 13 November; the third starts on the 11th."""
    first, second, third = portal.devices[:3]
    for offset, (a, b) in enumerate([(10, 1), (12, 1), (17, 2), (19, 2), (22, 3)]):
        day = FIRST + timedelta(days=offset)
        portal.set_day(day, a, first)
        portal.set_day(day, b, second)
        if offset >= 2:
            portal.set_day(day, 5 + offset, third)
    portal.last_sync = date(2026, 11, 13)
    return [first, second, third]


async def test_total_is_the_sum_of_the_meters(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test each day's total is the meters added up, as cumulative and as change."""
    at(freezer, "2026-11-15 10:00:00")
    meters = three_meters(portal)
    await setup_entry(hass, config_entry)

    total = await stat_rows(hass, TOTAL_ID)
    per_meter = [await stat_rows(hass, f"mijnted:meter_{m}") for m in meters]
    for day in (date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)):
        assert total[day]["state"] == sum(rows[day]["state"] for rows in per_meter)
    assert total[date(2026, 11, 13)]["state"] == 22 + 3 + 9
    # Change on the 13th: the household's units of that day.
    assert total[date(2026, 11, 13)]["sum"] - total[date(2026, 11, 12)]["sum"] == (
        22 + 3 + 9
    ) - (19 + 2 + 8)


async def test_days_missing_a_meter_are_left_out(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test a day on which one meter has no value yet is not summed partly."""
    at(freezer, "2026-11-15 10:00:00")
    three_meters(portal)
    await setup_entry(hass, config_entry)

    total = await stat_rows(hass, TOTAL_ID)
    assert date(2026, 11, 9) not in total
    assert date(2026, 11, 10) not in total
    assert min(total) == date(2026, 11, 11)


async def test_total_reimport_is_idempotent(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test further updates over the same days leave the total's rows unchanged."""
    at(freezer, "2026-11-15 10:00:00")
    three_meters(portal)
    await setup_entry(hass, config_entry)
    first = await stat_rows(hass, TOTAL_ID)

    await refresh(hass, config_entry)
    await refresh(hass, config_entry)
    assert await stat_rows(hass, TOTAL_ID) == first


async def test_statistic_names(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test meter statistics take the device name the user gave."""
    at(freezer, "2026-11-15 10:00:00")
    meters = three_meters(portal)
    await setup_entry(hass, config_entry)

    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, meters[1]), config_entry.entry_id
    )
    assert device is not None
    registry.async_update_device(device.id, name_by_user="Office")
    await refresh(hass, config_entry)
    await async_wait_recording_done(hass)

    ids = {TOTAL_ID, f"mijnted:meter_{meters[0]}", f"mijnted:meter_{meters[1]}"}
    metadata = await hass.async_add_executor_job(
        partial(get_metadata, hass, statistic_ids=ids)
    )
    names = {stat_id: meta["name"] for stat_id, (_, meta) in metadata.items()}
    assert names == {
        TOTAL_ID: "mijnTED total",
        f"mijnted:meter_{meters[0]}": f"Living room {meters[0]}",
        f"mijnted:meter_{meters[1]}": "Office",
    }
