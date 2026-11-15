"""Data update coordinator for mijnTED."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    BillingPeriod,
    MijntedAuthError,
    MijntedClient,
    MijntedConnectionError,
    MijntedUnexpectedPageError,
)
from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    HEATING_DELIVERY_TYPE,
    HISTORY_DAYS,
    ISSUE_EXPORT_DISABLED,
    ISSUE_UNEXPECTED_PAGE,
    MAX_BACKOFF,
    MAX_BACKOFF_STEPS,
)
from .models import Meter, MijntedData, ResidentialUnitInfo
from .season_tracker import SeasonTracker
from .statistics import (
    async_import_household,
    async_import_meter_statistics,
    household_statistic_id,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

type MijntedConfigEntry = ConfigEntry[MijntedCoordinator]


def season_year(periods: list[BillingPeriod], today: date) -> int:
    """Year the portal uses in its paths: the end year of the current billing period."""
    for period in periods:
        if period.active:
            return period.end.year
    for period in periods:
        if period.start <= today <= period.end:
            return period.end.year
    return today.year + 1 if today.month >= 6 else today.year


def portal_date(text: str | None) -> date | None:
    """Parse a portal date such as "31-05-2023 00:00:00"; None when empty or odd."""
    if not text:
        return None
    try:
        day, month, year = text.split()[0].split("-")
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _days_between(first: date, last: date) -> list[date]:
    """Every day from `first` up to and including `last`, if any."""
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


class MijntedCoordinator(DataUpdateCoordinator[MijntedData]):
    """Fetches meters and per-day values for all residential units of one account."""

    config_entry: MijntedConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: MijntedConfigEntry, client: MijntedClient
    ) -> None:
        """Initialise the coordinator."""
        hours = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS)
        self._base_interval = timedelta(hours=hours)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._base_interval,
        )
        self.client = client
        self._failures = 0
        # Per residential unit: days before the last readout. Those values are final, so
        # they are fetched once and kept for as long as they stay in the history window.
        self._final_days: dict[int, dict[date, dict[str, float]]] = {}
        self.seasons = SeasonTracker(hass, entry, client, self.async_update_listeners)

    async def _async_update_data(self) -> MijntedData:
        try:
            data = await self._async_fetch()
        except MijntedAuthError as err:
            _LOGGER.debug("Update: credentials rejected (%s)", err)
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except MijntedUnexpectedPageError as err:
            # Signing in again would show the same page. Stop polling until the entry is
            # reloaded, and tell the user through a repair issue.
            _LOGGER.debug("Update: %s", err)
            self.update_interval = None
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_UNEXPECTED_PAGE}_{self.config_entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_UNEXPECTED_PAGE,
                translation_placeholders={"step": err.step},
            )
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="unexpected_page",
                translation_placeholders={"step": err.step},
            ) from err
        except MijntedConnectionError as err:
            _LOGGER.debug("Update: cannot connect (%s)", err)
            # Capped: the interval reaches MAX_BACKOFF well before this, and an
            # ever-growing count would overflow the multiplication.
            self._failures = min(self._failures + 1, MAX_BACKOFF_STEPS)
            self.update_interval = min(
                self._base_interval * 2**self._failures, MAX_BACKOFF
            )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        self._failures = 0
        self.update_interval = self._base_interval
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{ISSUE_UNEXPECTED_PAGE}_{self.config_entry.entry_id}"
        )
        await async_import_meter_statistics(
            self.hass, list(data.meters.values()), self.config_entry.entry_id
        )
        for unit_id, totals in data.totals.items():
            await async_import_household(
                self.hass,
                household_statistic_id(unit_id, only_unit=len(data.units) == 1),
                totals,
                [
                    meter.days
                    for meter in data.meters.values()
                    if meter.residential_unit_id == unit_id
                ],
            )
        await self.seasons.async_update(data)
        return data

    async def _async_fetch(self) -> MijntedData:
        today = dt_util.now().date()
        data = MijntedData()
        for unit_id in await self.client.async_get_residential_unit_ids():
            unit = await self.client.async_get_residential_unit(unit_id)
            issue_id = f"{ISSUE_EXPORT_DISABLED}_{unit_id}"
            if not unit.export_active:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_EXPORT_DISABLED,
                    translation_placeholders={
                        "apartment": unit.apartment_no or str(unit_id)
                    },
                )
                continue
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

            delivery_types = await self.client.async_get_delivery_types(unit_id) or [
                HEATING_DELIVERY_TYPE
            ]
            year = season_year(
                await self.client.async_get_billing_periods(unit_id), today
            )
            model = await self.client.async_get_model(unit_id, delivery_types[0])
            data.units[unit_id] = ResidentialUnitInfo(
                id=unit_id,
                billing_unit_id=unit.billing_unit_id,
                apartment_no=unit.apartment_no,
                model=model,
            )

            meters: dict[str, Meter] = {}
            for delivery_type in delivery_types:
                readout = await self.client.async_get_last_sync_date(
                    unit_id, delivery_type, year
                )
                if readout is not None:
                    readout = min(readout, today)
                for status in await self.client.async_get_device_statuses(
                    unit_id, delivery_type, year, today
                ):
                    meters[status.device_number] = Meter(
                        device_number=status.device_number,
                        residential_unit_id=unit_id,
                        room_code=status.room,
                        unit_of_measure=status.unit_of_measure,
                        model=model,
                        last_readout=readout,
                        deactivation_date=portal_date(status.deactivation_date),
                    )

            data.totals[unit_id] = await self._async_fill_days(
                unit.billing_unit_id, unit_id, meters, today
            )
            data.meters.update(meters)
            readouts = [meter.last_readout for meter in meters.values()]
            data.units[unit_id].last_readout = max(
                (day for day in readouts if day), default=None
            )
        return data

    async def _async_fill_days(
        self, billing_unit_id: int, unit_id: int, meters: dict[str, Meter], today: date
    ) -> dict[date, float]:
        """Fetch the per-day values from the window start up to the last readout.

        Returns the household total per day: the sum over the unit's meters, for
        days on which every meter has a value. A day that some meter still lacks
        (lag, weekend) is left out, so the total never shows a day too low. Meters
        without any value in the window, such as removed ones, do not count.
        """
        readouts = [
            meter.last_readout for meter in meters.values() if meter.last_readout
        ]
        if not readouts:
            return {}
        last_readout = max(readouts)
        window_start = today - timedelta(days=HISTORY_DAYS)
        wanted = _days_between(window_start, last_readout)
        if not wanted:
            return {}
        values_by_day = await self._async_values_by_day(
            billing_unit_id, unit_id, wanted, last_readout
        )
        for day in wanted:
            for device_number, value in values_by_day.get(day, {}).items():
                meter = meters.get(device_number)
                if meter and meter.last_readout and day <= meter.last_readout:
                    meter.days[day] = value
        counting = [meter for meter in meters.values() if meter.days]
        return {
            day: sum(meter.days[day] for meter in counting)
            for day in wanted
            if counting and all(day in meter.days for meter in counting)
        }

    async def _async_values_by_day(
        self, billing_unit_id: int, unit_id: int, wanted: list[date], last_readout: date
    ) -> dict[date, dict[str, float]]:
        """Return the export of each wanted day, fetching only days not cached.

        A day before the last readout is final and kept, but only once it is
        complete. It is incomplete when a meter with a value on an earlier day has
        none on it; such a day is fetched again next time. Days before a meter's
        first value, such as before it was installed, do not wait for it.
        """
        cache = self._final_days.setdefault(unit_id, {})
        for day in [day for day in cache if day < wanted[0]]:
            del cache[day]
        fetched = await self.client.async_get_days(
            billing_unit_id, unit_id, [day for day in wanted if day not in cache]
        )
        # Keep a day only when it has every meter that had a value before it.
        seen: set[str] = set()
        complete: set[date] = set()
        for day in sorted(cache.keys() | fetched.keys()):
            values = fetched.get(day, cache.get(day, {}))
            if seen <= values.keys():
                complete.add(day)
            seen |= values.keys()
        for day, values in fetched.items():
            if day < last_readout and day in complete:
                cache[day] = values
        return {**cache, **fetched}
