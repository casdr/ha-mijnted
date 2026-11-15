"""Import per-day meter values as external statistics, dated on the day they belong to.

Each day gets one hourly row at local midnight with the cumulative season value as
`state` and a running `sum`. The sum continues from the last row before the imported
window, so importing the same days again overwrites the rows with the same values
instead of adding to them.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, UNIT_UNITS
from .models import Meter, daily_changes, meter_name

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .seasons import PreviousSeason

_LOGGER = logging.getLogger(__name__)

# How far back to look for the row the running sum continues from.
LOOKBACK = timedelta(days=400)
# Rows imported since Home Assistant started, per statistic: day -> (state, sum).
# The recorder writes imports later, from its queue; these rows let the next import
# continue the sum without reading rows that may not be written yet.
_IMPORTED: HassKey[dict[str, dict[date, tuple[float, float]]]] = HassKey(
    f"{DOMAIN}_imported_statistics"
)


def statistic_id(device_number: str) -> str:
    """External statistic id of a meter."""
    return f"{DOMAIN}:meter_{device_number.lower()}"


def _unit_statistic_id(name: str, unit_id: int, *, only_unit: bool) -> str:
    base = f"{DOMAIN}:{name}"
    return base if only_unit else f"{base}_{unit_id}"


def previous_season_statistic_id(unit_id: int, *, only_unit: bool) -> str:
    """External statistic id of the previous season's total of a residential unit.

    With one residential unit, the usual case, it is `mijnted:total_previous_season`.
    Accounts with several units get the unit id appended, so they do not overwrite
    each other.
    """
    return _unit_statistic_id("total_previous_season", unit_id, only_unit=only_unit)


def household_statistic_id(unit_id: int, *, only_unit: bool) -> str:
    """External statistic id of the household total of a residential unit.

    `mijnted:total`, with the unit id appended for accounts with several units,
    like the previous season's.
    """
    return _unit_statistic_id("total", unit_id, only_unit=only_unit)


def _last_row_before(
    hass: HomeAssistant, stat_id: str, before: datetime
) -> tuple[date, float | None, float] | None:
    """Day, state and sum of the newest stored row before `before`, if any."""
    rows = statistics_during_period(
        hass, before - LOOKBACK, before, {stat_id}, "hour", None, {"state", "sum"}
    ).get(stat_id, [])
    if not rows:
        return None
    last = rows[-1]
    day = dt_util.as_local(dt_util.utc_from_timestamp(last["start"])).date()
    return day, last.get("state"), last.get("sum") or 0.0


async def _async_import_days(
    hass: HomeAssistant, stat_id: str, name: str, days: dict[date, float]
) -> None:
    """Import one row per day, with the sum continuing from the row before."""
    if not days:
        return
    first_day = min(days)
    imported = hass.data.setdefault(_IMPORTED, {}).setdefault(stat_id, {})
    # The newest row before the first day: stored by an earlier run, or imported in
    # this run and perhaps still in the recorder's queue. The later one counts.
    stored = await get_instance(hass).async_add_executor_job(
        _last_row_before, hass, stat_id, dt_util.start_of_local_day(first_day)
    )
    earlier = [day for day in imported if day < first_day]
    previous_state: float | None = None
    running_sum = 0.0
    if stored is not None:
        _, previous_state, running_sum = stored
    if earlier and (stored is None or max(earlier) >= stored[0]):
        previous_state, running_sum = imported[max(earlier)]

    for day in [day for day in imported if day < first_day - LOOKBACK]:
        del imported[day]
    rows: list[StatisticData] = []
    for day, value, change in daily_changes(days, previous_state):
        running_sum += change
        imported[day] = (value, running_sum)
        rows.append(
            StatisticData(
                start=dt_util.start_of_local_day(day), state=value, sum=running_sum
            )
        )
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class=None,
        unit_of_measurement=UNIT_UNITS,
    )
    _LOGGER.debug("Importing %d days into %s", len(rows), stat_id)
    async_add_external_statistics(hass, metadata, rows)


async def async_import_meter_statistics(
    hass: HomeAssistant, meters: list[Meter], entry_id: str | None = None
) -> None:
    """Import the per-day values of the given meters.

    A statistic is named after its device in the config entry `entry_id`: the
    name the user gave it, otherwise the room name from the portal plus the device
    number.
    """
    language = hass.config.language
    devices = dr.async_get(hass)
    for meter in meters:
        device = (
            devices.async_get_device_by_identifier(
                (DOMAIN, meter.device_number), entry_id
            )
            if entry_id
            else None
        )
        name = (device.name_by_user if device else None) or meter_name(
            meter.device_number, meter.room_code, language
        )
        await _async_import_days(
            hass, statistic_id(meter.device_number), name, meter.days
        )


async def async_import_previous_season(
    hass: HomeAssistant, stat_id: str, previous: PreviousSeason
) -> None:
    """Import the previous season's household total, moved forward one year.

    The whole season is imported, also the days still to come this year. After a
    new season starts, the season that just ended is imported over its own dates
    while the rows of the season before stay in the past.
    """
    name = (
        "Vorig seizoen totaal"
        if hass.config.language.lower().startswith("nl")
        else "Previous season total"
    )
    await _async_import_days(hass, stat_id, name, previous.shifted())


async def async_import_household(
    hass: HomeAssistant, stat_id: str, days: dict[date, float]
) -> None:
    """Import the household total (all meters added up) per day."""
    await _async_import_days(hass, stat_id, "mijnTED total", days)
