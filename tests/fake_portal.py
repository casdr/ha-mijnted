"""A simulated mijnTED portal and the constants the tests share."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from custom_components.mijnted.api import (
    BillingPeriod,
    DeviceStatus,
    MijntedError,
    ResidentialUnit,
)

FIXTURES = Path(__file__).parent / "fixtures"
EMAIL = "tester@example.com"
PASSWORD = "correct-horse"
ACCOUNT_ID = "00000000-0000-0000-0000-00000000abcd"
UNIT_ID = 100001
BILLING_UNIT_ID = 2001


def load_json(name: str) -> Any:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text())


def load_text(name: str) -> str:
    """Load a text fixture."""
    return (FIXTURES / name).read_text()


class FakePortal:
    """In-memory stand-in for MijntedClient, driven by per-day values.

    `values[day][device]` is what the export returns for that day. `last_sync` is what
    getLastSyncDate returns. Calls are counted, so tests can check what was fetched.
    """

    def __init__(self) -> None:
        """Start without values or failures."""
        self.statuses = [
            DeviceStatus(
                device_number=item["deviceNumber"],
                room=item["room"],
                reading=item["currentReadingValue"],
                unit_of_measure=item["unitOfMeasure"],
                installation_date=item["installationDate"],
                deactivation_date=item["deactivationDate"] or None,
            )
            for item in load_json("device_statuses.json")
        ]
        self.values: dict[date, dict[str, float]] = {}
        self.last_sync: date | None = None
        self.export_active = True
        self.fail_with: MijntedError | None = None
        self.fetched_days: list[date] = []
        self.backfill_days: list[date] = []
        self.sign_ins = 0
        self.account_id: str | None = ACCOUNT_ID

    @property
    def devices(self) -> list[str]:
        """Device numbers of the meters."""
        return [status.device_number for status in self.statuses]

    def set_day(self, day: date, value: float, device: str | None = None) -> None:
        """Set a day's value of one device, by default the first."""
        device = device or self.devices[0]
        self.values.setdefault(day, {})[device] = value

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def async_sign_in(self) -> str:
        """Count the sign-in and return the account id."""
        self.sign_ins += 1
        self._check()
        return ACCOUNT_ID

    async def async_get_residential_unit_ids(self) -> list[int]:
        """Return the one residential unit."""
        self._check()
        return [UNIT_ID]

    async def async_get_residential_unit(self, unit_id: int) -> ResidentialUnit:
        """Return the residential unit and its billing unit."""
        return ResidentialUnit(
            id=unit_id,
            billing_unit_id=BILLING_UNIT_ID,
            apartment_no="1",
            export_active=self.export_active,
        )

    async def async_get_delivery_types(self, _unit_id: int) -> list[int]:
        """Return heating as the only delivery type."""
        return [1]

    async def async_get_billing_periods(self, _unit_id: int) -> list[BillingPeriod]:
        """Return the 2026/2027 heating season."""
        return [BillingPeriod(date(2026, 6, 1), date(2027, 5, 31), active=True)]

    async def async_get_model(self, _unit_id: int, _delivery_type: int) -> str | None:
        """Return the meter model."""
        return "F71"

    async def async_get_last_sync_date(
        self, _unit_id: int, _delivery_type: int, _year: int
    ) -> date | None:
        """Return the last readout date."""
        return self.last_sync

    async def async_get_device_statuses(
        self, _unit_id: int, _delivery_type: int, _year: int, _from_date: date
    ) -> list[DeviceStatus]:
        """Return the meters from the recorded fixture."""
        return self.statuses

    async def async_get_days(
        self, _billing_unit_id: int, _unit_id: int, days: list[date]
    ) -> dict[date, dict[str, float]]:
        """Return the export of each day and record which days were asked."""
        self._check()
        self.fetched_days.extend(days)
        return {day: self._export(day) for day in days}

    async def async_get_day_values(
        self, _billing_unit_id: int, _unit_id: int, day: date
    ) -> dict[str, float]:
        """Return the export of one day, as the previous-season backfill asks."""
        self._check()
        self.backfill_days.append(day)
        return self._export(day)

    def fill_ramp(self, first: date, last: date, final: float) -> None:
        """Let the first meter count up evenly from 0 on `first` to `final`."""
        days = (last - first).days
        for offset in range(days + 1):
            self.set_day(first + timedelta(days=offset), round(final * offset / days))

    def _export(self, day: date) -> dict[str, float]:
        """Export a day: a day without its own value repeats the newest earlier one."""
        result: dict[str, float] = {}
        for device in self.devices:
            earlier = [
                (d, values[device])
                for d, values in self.values.items()
                if d <= day and device in values
            ]
            if earlier:
                result[device] = max(earlier)[1]
        return result
