"""Keep the previous heating season per residential unit, fetched once per season.

The previous season is about 365 daily exports. They are fetched in the background
at a few requests per second and kept in `.storage`, so a restart does not fetch them
again. A reset in the recent days, or a total lower than the last one seen, means a
new season started: then the season that just ended is fetched as the new previous
season.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .api import MijntedError
from .const import DOMAIN
from .seasons import SeasonHistory, async_find_seasons, is_reset, reset_after
from .statistics import (
    async_import_household,
    async_import_meter_previous_seasons,
    async_import_previous_season,
    household_statistic_id,
    previous_season_statistic_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .api import MijntedClient
    from .models import Meter, MijntedData, ResidentialUnitInfo
    from .seasons import PreviousSeason

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# Pause before each request of the backfill: at most two requests per second.
BACKFILL_DELAY = 0.5
# Write the last seen totals at most this often (seconds).
SAVE_DELAY = 60


def store_key(entry_id: str) -> str:
    """Storage key of a config entry's seasons."""
    return f"{DOMAIN}.{entry_id}.seasons"


def _complete_days(
    totals: dict[date, float], meters_by_day: dict[date, set[str]]
) -> dict[date, float]:
    """Keep the days on which every meter seen in these days has a value."""
    meters = set().union(*(meters_by_day.get(day, set()) for day in totals))
    return {
        day: total
        for day, total in totals.items()
        if meters <= meters_by_day.get(day, set())
    }


def _per_meter(
    days: dict[date, float], values_by_day: dict[date, dict[str, float]]
) -> dict[str, dict[date, float]]:
    """Each meter's values on the given days, from the fetched exports."""
    meters: dict[str, dict[date, float]] = {}
    for day in sorted(days):
        for device_number, value in values_by_day.get(day, {}).items():
            meters.setdefault(device_number, {})[day] = value
    return meters


class SeasonTracker:
    """The previous season of each residential unit of one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry[Any],
        client: MijntedClient,
        on_change: Callable[[], None],
    ) -> None:
        """Initialize the tracker; `on_change` runs when a previous season changes."""
        self._hass = hass
        self._entry = entry
        self._client = client
        self._on_change = on_change
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, store_key(entry.entry_id)
        )
        self._history: dict[int, SeasonHistory] = {}
        # Newest (day, household total) seen per unit, to notice a reset that fell
        # outside the window of recent days, for example after a long downtime.
        self._last_seen: dict[int, tuple[date, float]] = {}
        self._imported: set[int] = set()
        # Units whose previous season must be fetched (again) until it succeeds.
        self._pending: set[int] = set()
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._loaded = False
        # The meters of the last update, to name the per-meter statistics.
        self._meters: dict[str, Meter] = {}

    def previous(self, unit_id: int) -> PreviousSeason | None:
        """Return the previous season of a unit, None while unknown."""
        history = self._history.get(unit_id)
        return history.previous if history else None

    def current_start(self, unit_id: int) -> date | None:
        """Return the first day of the current season of a unit, if known."""
        history = self._history.get(unit_id)
        return history.current_start if history else None

    @property
    def backfilling(self) -> bool:
        """Whether a previous season is being fetched."""
        return any(not task.done() for task in self._tasks.values())

    async def async_update(self, data: MijntedData) -> None:
        """Check each unit after a coordinator update; start fetching when needed."""
        if not self._loaded:
            await self._async_load()
        units = data.units
        self._meters = data.meters
        for unit_id, unit in units.items():
            unit_totals = data.totals.get(unit_id, {})
            if unit_id not in self._imported and self.previous(unit_id):
                await self._async_import(unit_id, units)
            if not unit_totals:
                continue
            if self._needs_backfill(unit_id, unit_totals):
                self._start_backfill(unit_id, unit, max(unit_totals), units)
            newest = max(unit_totals)
            self._last_seen[unit_id] = (newest, unit_totals[newest])
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)

    def _needs_backfill(self, unit_id: int, totals: dict[date, float]) -> bool:
        history = self._history.get(unit_id)
        if history is None or unit_id in self._pending:
            return True
        if history.previous and not history.previous.meters:
            # Stored before the meters were kept: fetch once more to get them.
            return True
        if reset_after(totals, history.current_start) is not None:
            return True
        last_seen = self._last_seen.get(unit_id)
        if last_seen is None:
            return False
        seen_day, seen_total = last_seen
        newer = [day for day in totals if day > seen_day]
        return bool(newer) and is_reset(seen_total, totals[min(newer)])

    def _start_backfill(
        self,
        unit_id: int,
        unit: ResidentialUnitInfo,
        latest: date,
        units: dict[int, ResidentialUnitInfo],
    ) -> None:
        task = self._tasks.get(unit_id)
        if task and not task.done():
            return
        self._pending.add(unit_id)
        self._tasks[unit_id] = self._entry.async_create_background_task(
            self._hass,
            self._async_backfill(unit_id, unit.billing_unit_id, latest, units),
            f"{DOMAIN} previous season {unit_id}",
        )

    async def _async_backfill(
        self,
        unit_id: int,
        billing_unit_id: int,
        latest: date,
        units: dict[int, ResidentialUnitInfo],
    ) -> None:
        """Find the seasons of a unit and fetch the previous one, throttled."""
        values_by_day: dict[date, dict[str, float]] = {}

        async def fetch(day: date) -> float | None:
            await asyncio.sleep(BACKFILL_DELAY)
            values = await self._client.async_get_day_values(
                billing_unit_id, unit_id, day
            )
            values_by_day[day] = values
            return sum(values.values()) if values else None

        _LOGGER.debug("Fetching the previous season")
        try:
            history = await async_find_seasons(fetch, latest)
        except MijntedError as err:
            # Messages hold only a step, HTTP status or exception type.
            _LOGGER.warning(
                "Fetching the previous season failed, trying again at the next "
                "update: %s",
                err,
            )
            return
        if history.previous:
            history.previous.meters = _per_meter(history.previous.days, values_by_day)
        self._history[unit_id] = history
        self._pending.discard(unit_id)
        await self._store.async_save(self._as_dict())
        self._imported.discard(unit_id)
        if history.previous:
            await self._async_import(unit_id, units)
        await async_import_household(
            self._hass,
            household_statistic_id(unit_id, only_unit=len(units) == 1),
            _complete_days(
                history.current_days,
                {day: set(values) for day, values in values_by_day.items()},
            ),
        )
        _LOGGER.debug(
            "Current season started %s, previous season has %d days",
            history.current_start,
            len(history.previous.days) if history.previous else 0,
        )
        self._on_change()

    async def _async_import(
        self, unit_id: int, units: dict[int, ResidentialUnitInfo]
    ) -> None:
        previous = self.previous(unit_id)
        if previous is None:
            return
        stat_id = previous_season_statistic_id(unit_id, only_unit=len(units) == 1)
        await async_import_previous_season(self._hass, stat_id, previous)
        await async_import_meter_previous_seasons(
            self._hass, previous, self._meters, self._entry.entry_id
        )
        self._imported.add(unit_id)

    async def _async_load(self) -> None:
        self._loaded = True
        stored = await self._store.async_load() or {}
        for key, data in stored.get("units", {}).items():
            self._history[int(key)] = SeasonHistory.from_dict(data)
            seen = data.get("last_seen")
            if seen:
                self._last_seen[int(key)] = (date.fromisoformat(seen[0]), seen[1])

    def _as_dict(self) -> dict[str, Any]:
        units: dict[str, Any] = {}
        for unit_id, history in self._history.items():
            data = history.as_dict()
            if seen := self._last_seen.get(unit_id):
                data["last_seen"] = [seen[0].isoformat(), seen[1]]
            units[str(unit_id)] = data
        return {"units": units}
