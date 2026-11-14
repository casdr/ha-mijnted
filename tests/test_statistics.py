"""Tests for importing per-day values as dated statistics."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.mijnted.models import Meter
from custom_components.mijnted.statistics import (
    async_import_meter_statistics,
    statistic_id,
)

DEVICE = "90000001"
STAT_ID = statistic_id(DEVICE)


def meter(days: dict[date, float]) -> Meter:
    return Meter(
        device_number=DEVICE,
        residential_unit_id=1,
        room_code="W",
        unit_of_measure="Eenheden",
        model="F71",
        last_readout=max(days),
        days=days,
    )


async def import_days(hass: HomeAssistant, days: dict[date, float]) -> None:
    await async_import_meter_statistics(hass, [meter(days)])
    await async_wait_recording_done(hass)


async def rows(hass: HomeAssistant) -> dict[date, tuple[float, float]]:
    """(state, sum) per local day."""
    start = dt_util.start_of_local_day(date(2026, 1, 1))
    result = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {STAT_ID},
        "hour",
        None,
        {"state", "sum"},
    )
    out: dict[date, tuple[float, float]] = {}
    for row in result.get(STAT_ID, []):
        day = dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date()
        out[day] = (row["state"], row["sum"])
    return out


async def daily_change(hass: HomeAssistant) -> dict[date, float]:
    """What the statistics graph shows as 'change' per day."""
    start = dt_util.start_of_local_day(date(2026, 1, 1))
    result = await hass.async_add_executor_job(
        statistics_during_period, hass, start, None, {STAT_ID}, "day", None, {"change"}
    )
    return {
        dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date(): row["change"]
        for row in result.get(STAT_ID, [])
    }


def days_from(first: date, values: list[float]) -> dict[date, float]:
    return {
        first + timedelta(days=offset): value for offset, value in enumerate(values)
    }


@pytest.fixture
async def setup(hass: HomeAssistant, amsterdam: Any) -> None:
    """Recorder plus the portal's time zone."""


@pytest.mark.usefixtures("setup")
async def test_rows_land_on_their_own_date(hass: HomeAssistant) -> None:
    await import_days(hass, days_from(date(2026, 11, 10), [10, 12, 17, 22]))
    assert await rows(hass) == {
        date(2026, 11, 10): (10, 0),
        date(2026, 11, 11): (12, 2),
        date(2026, 11, 12): (17, 7),
        date(2026, 11, 13): (22, 12),
    }
    changes = await daily_change(hass)
    assert changes[date(2026, 11, 13)] == 5
    assert changes[date(2026, 11, 11)] == 2


@pytest.mark.usefixtures("setup")
async def test_reimport_is_idempotent(hass: HomeAssistant) -> None:
    days = days_from(date(2026, 11, 10), [10, 12, 17, 22])
    await import_days(hass, days)
    first = await rows(hass)
    await import_days(hass, days)
    await import_days(hass, days)
    assert await rows(hass) == first


@pytest.mark.usefixtures("setup")
async def test_overlapping_window_continues_the_sum(hass: HomeAssistant) -> None:
    """A later window that overlaps the first keeps the sums of the overlapping days."""
    await import_days(hass, days_from(date(2026, 11, 1), [1, 2, 4, 7, 11]))
    await import_days(hass, days_from(date(2026, 11, 3), [4, 7, 11, 16, 22]))
    result = await rows(hass)
    assert result[date(2026, 11, 5)] == (11, 10)
    assert result[date(2026, 11, 7)] == (22, 21)
    assert sum((await daily_change(hass)).values()) == 21


@pytest.mark.usefixtures("setup")
async def test_weekend_values_arrive_on_monday(hass: HomeAssistant) -> None:
    """Saturday and Sunday are imported on Monday, each with its own value."""
    # Saturday's update: readouts up to Friday the 13th.
    await import_days(hass, days_from(date(2026, 11, 9), [10, 12, 15, 17, 20]))
    # Monday's update: Saturday, Sunday and Monday arrive together.
    await import_days(
        hass, days_from(date(2026, 11, 9), [10, 12, 15, 17, 20, 26, 31, 33])
    )
    changes = await daily_change(hass)
    assert changes[date(2026, 11, 14)] == 6
    assert changes[date(2026, 11, 15)] == 5
    assert changes[date(2026, 11, 16)] == 2


@pytest.mark.usefixtures("setup")
async def test_season_reset_does_not_go_negative(hass: HomeAssistant) -> None:
    await import_days(hass, days_from(date(2026, 5, 29), [487, 489, 0, 0, 2]))
    result = await rows(hass)
    assert [state for state, _ in result.values()] == [487, 489, 0, 0, 2]
    assert [total for _, total in result.values()] == [0, 2, 2, 2, 4]
    assert min((await daily_change(hass)).values()) >= 0
