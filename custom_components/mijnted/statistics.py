"""Import per-day meter values as external statistics, dated on the day they belong to.

Each day gets one hourly row at local midnight with the cumulative season value as `state` and a
running `sum`. The sum continues from the last row before the imported window, so importing the
same days again overwrites the rows with the same values instead of adding to them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

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
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, UNIT_UNITS
from .models import Meter, daily_changes, meter_name

_LOGGER = logging.getLogger(__name__)

# How far back to look for the row the running sum continues from.
LOOKBACK = timedelta(days=400)


def statistic_id(device_number: str) -> str:
    """External statistic id of a meter."""
    return f"{DOMAIN}:meter_{device_number.lower()}"


def _last_row_before(
    hass: HomeAssistant, stat_id: str, before: datetime
) -> tuple[float | None, float]:
    """State and sum of the newest row before `before`; (None, 0.0) when there is none."""
    rows = statistics_during_period(
        hass, before - LOOKBACK, before, {stat_id}, "hour", None, {"state", "sum"}
    ).get(stat_id, [])
    if not rows:
        return None, 0.0
    last = rows[-1]
    return last.get("state"), last.get("sum") or 0.0


async def async_import_meter_statistics(hass: HomeAssistant, meters: list[Meter]) -> None:
    """Import the per-day values of the given meters."""
    language = hass.config.language
    for meter in meters:
        if not meter.days:
            continue
        stat_id = statistic_id(meter.device_number)
        first_day = min(meter.days)
        previous_state, running_sum = await get_instance(hass).async_add_executor_job(
            _last_row_before, hass, stat_id, dt_util.start_of_local_day(first_day)
        )
        rows: list[StatisticData] = []
        for day, value, change in daily_changes(meter.days, previous_state):
            running_sum += change
            rows.append(
                StatisticData(start=dt_util.start_of_local_day(day), state=value, sum=running_sum)
            )
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=meter_name(meter.device_number, meter.room_code, language),
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=None,
            unit_of_measurement=UNIT_UNITS,
        )
        _LOGGER.debug("Importing %d days for meter %s", len(rows), meter.device_number)
        async_add_external_statistics(hass, metadata, rows)
