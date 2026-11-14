"""A simulated mijnTED portal and the constants the tests share."""

from __future__ import annotations

import json
from datetime import date
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
        self.sign_ins = 0
        self.account_id: str | None = ACCOUNT_ID

    @property
    def devices(self) -> list[str]:
        return [status.device_number for status in self.statuses]

    def set_day(self, day: date, value: float, device: str | None = None) -> None:
        """Set one device (default: the first) on a day; other devices copy earlier values."""
        device = device or self.devices[0]
        self.values.setdefault(day, {})[device] = value

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def async_sign_in(self) -> str:
        self.sign_ins += 1
        self._check()
        return ACCOUNT_ID

    async def async_get_residential_unit_ids(self) -> list[int]:
        self._check()
        return [UNIT_ID]

    async def async_get_residential_unit(self, unit_id: int) -> ResidentialUnit:
        return ResidentialUnit(unit_id, BILLING_UNIT_ID, "1", self.export_active)

    async def async_get_delivery_types(self, unit_id: int) -> list[int]:
        return [1]

    async def async_get_billing_periods(self, unit_id: int) -> list[BillingPeriod]:
        return [BillingPeriod(date(2026, 6, 1), date(2027, 5, 31), True)]

    async def async_get_model(self, unit_id: int, delivery_type: int) -> str | None:
        return "F71"

    async def async_get_last_sync_date(
        self, unit_id: int, delivery_type: int, year: int
    ) -> date | None:
        return self.last_sync

    async def async_get_device_statuses(
        self, unit_id: int, delivery_type: int, year: int, from_date: date
    ) -> list[DeviceStatus]:
        return self.statuses

    async def async_get_days(
        self, billing_unit_id: int, unit_id: int, days: list[date]
    ) -> dict[date, dict[str, float]]:
        self._check()
        self.fetched_days.extend(days)
        return {day: self._export(day) for day in days}

    def _export(self, day: date) -> dict[str, float]:
        """Like the portal: a day without its own value repeats the newest earlier value."""
        result: dict[str, float] = {}
        for device in self.devices:
            earlier = [d for d in self.values if d <= day and device in self.values[d]]
            if earlier:
                result[device] = self.values[max(earlier)][device]
        return result
