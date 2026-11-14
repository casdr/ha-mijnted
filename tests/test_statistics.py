"""Tests for importing per-day values as dated statistics."""

from __future__ import annotations

from datetime import date, timedelta
from functools import partial
from typing import TYPE_CHECKING, Literal

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.mijnted.models import Meter
from custom_components.mijnted.statistics import (
    async_import_meter_statistics,
    statistic_id,
)

if TYPE_CHECKING:
    from homeassistant.components.recorder.statistics import StatisticsRow
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("amsterdam")

DEVICE = "90000001"
STAT_ID = statistic_id(DEVICE)


def meter(days: dict[date, float]) -> Meter:
    """Return a meter with the given daily values."""
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
    """Import the days and wait for the recorder."""
    await async_import_meter_statistics(hass, [meter(days)])
    await async_wait_recording_done(hass)


START = date(2026, 1, 1)


def _query(
    hass: HomeAssistant,
    period: Literal["hour", "day"],
    types: set[Literal["change", "last_reset", "max", "mean", "min", "state", "sum"]],
) -> dict[date, StatisticsRow]:
    """Return the statistic's rows by local day."""
    result = statistics_during_period(
        hass, dt_util.start_of_local_day(START), None, {STAT_ID}, period, None, types
    )
    return {
        dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date(): row
        for row in result.get(STAT_ID, [])
    }


async def rows(hass: HomeAssistant) -> dict[date, tuple[float | None, float | None]]:
    """Return (state, sum) per local day."""
    found = await hass.async_add_executor_job(
        partial(_query, hass, "hour", {"state", "sum"})
    )
    return {day: (row.get("state"), row.get("sum")) for day, row in found.items()}


async def daily_change(hass: HomeAssistant) -> dict[date, float]:
    """Return what the statistics graph shows as change per day."""
    found = await hass.async_add_executor_job(partial(_query, hass, "day", {"change"}))
    return {day: row.get("change") or 0.0 for day, row in found.items()}


def days_from(first: date, values: list[float]) -> dict[date, float]:
    """Map consecutive days, starting on `first`, to the values."""
    return {
        first + timedelta(days=offset): value for offset, value in enumerate(values)
    }


async def test_rows_land_on_their_own_date(hass: HomeAssistant) -> None:
    """Test rows land on their own date."""
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


async def test_reimport_is_idempotent(hass: HomeAssistant) -> None:
    """Test reimport is idempotent."""
    days = days_from(date(2026, 11, 10), [10, 12, 17, 22])
    await import_days(hass, days)
    first = await rows(hass)
    await import_days(hass, days)
    await import_days(hass, days)
    assert await rows(hass) == first


async def test_overlapping_window_continues_the_sum(hass: HomeAssistant) -> None:
    """A later window that overlaps the first keeps the sums of the overlapping days."""
    await import_days(hass, days_from(date(2026, 11, 1), [1, 2, 4, 7, 11]))
    await import_days(hass, days_from(date(2026, 11, 3), [4, 7, 11, 16, 22]))
    result = await rows(hass)
    assert result[date(2026, 11, 5)] == (11, 10)
    assert result[date(2026, 11, 7)] == (22, 21)
    assert sum((await daily_change(hass)).values()) == 21


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


async def test_season_reset_does_not_go_negative(hass: HomeAssistant) -> None:
    """Test season reset does not go negative."""
    await import_days(hass, days_from(date(2026, 5, 29), [487, 489, 0, 0, 2]))
    result = await rows(hass)
    assert [state for state, _ in result.values()] == [487, 489, 0, 0, 2]
    assert [total for _, total in result.values()] == [0, 2, 2, 2, 4]
    assert min((await daily_change(hass)).values()) >= 0
