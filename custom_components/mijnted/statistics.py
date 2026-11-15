"""Import per-day meter values as external statistics, dated on the day they belong to.

Each day gets one hourly row at local midnight with the cumulative season value as
`state` and a running `sum`. The sum continues from the last row before the imported
window, so importing the same days again overwrites the rows with the same values
instead of adding to them.
"""

from __future__ import annotations

import logging
import time
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
# A statistic queued this recently may not be written yet; wait for the recorder.
RECENT_IMPORT = 60.0
_QUEUED: HassKey[dict[str, float]] = HassKey(f"{DOMAIN}_queued_statistics")


def statistic_id(device_number: str) -> str:
    """External statistic id of a meter."""
    return f"{DOMAIN}:meter_{device_number.lower()}"


def previous_season_statistic_id(unit_id: int, *, only_unit: bool) -> str:
    """External statistic id of the previous season's total of a residential unit.

    With one residential unit, the usual case, it is `mijnted:total_previous_season`.
    Accounts with several units get the unit id appended, so they do not overwrite
    each other.
    """
    base = f"{DOMAIN}:total_previous_season"
    return base if only_unit else f"{base}_{unit_id}"


def _last_row_before(
    hass: HomeAssistant, stat_id: str, before: datetime
) -> tuple[float | None, float]:
    """State and sum of the newest row before `before`, or (None, 0.0) without one."""
    rows = statistics_during_period(
        hass, before - LOOKBACK, before, {stat_id}, "hour", None, {"state", "sum"}
    ).get(stat_id, [])
    if not rows:
        return None, 0.0
    last = rows[-1]
    return last.get("state"), last.get("sum") or 0.0


async def _async_import_days(
    hass: HomeAssistant, stat_id: str, name: str, days: dict[date, float]
) -> None:
    """Import one row per day, with the sum continuing from the row before."""
    if not days:
        return
    recorder = get_instance(hass)
    queued = hass.data.setdefault(_QUEUED, {})
    if time.monotonic() - queued.get(stat_id, -RECENT_IMPORT) < RECENT_IMPORT:
        # The sum continues from rows a previous import only queued. The recorder
        # works through its queue in order, so waiting for the queue to drain
        # means those rows are written; otherwise the sum would restart at zero.
        await hass.async_add_executor_job(recorder.block_till_done)
    previous_state, running_sum = await recorder.async_add_executor_job(
        _last_row_before, hass, stat_id, dt_util.start_of_local_day(min(days))
    )
    rows: list[StatisticData] = []
    for day, value, change in daily_changes(days, previous_state):
        running_sum += change
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
    queued[stat_id] = time.monotonic()


async def async_import_meter_statistics(
    hass: HomeAssistant, meters: list[Meter]
) -> None:
    """Import the per-day values of the given meters."""
    language = hass.config.language
    for meter in meters:
        await _async_import_days(
            hass,
            statistic_id(meter.device_number),
            meter_name(meter.device_number, meter.room_code, language),
            meter.days,
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
