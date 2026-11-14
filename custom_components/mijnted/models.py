"""Data model for mijnTED readings, independent of Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .const import ROOMS_EN, ROOMS_NL


def meter_name(device_number: str, room_code: str | None, language: str) -> str:
    """Display name for a meter: room name plus device number, as rooms often repeat."""
    if not room_code:
        return device_number
    rooms = ROOMS_NL if language.lower().startswith("nl") else ROOMS_EN
    return f"{rooms.get(room_code, room_code)} {device_number}"


@dataclass(slots=True)
class Meter:
    """One heat cost allocator and its per-day cumulative values.

    `days` holds only days up to and including the last readout date. Days after it repeat the
    last value in the portal and are not real readings.
    """

    device_number: str
    residential_unit_id: int
    room_code: str | None
    unit_of_measure: str | None
    model: str | None
    last_readout: date | None
    days: dict[date, float] = field(default_factory=dict)

    @property
    def readout_value(self) -> float | None:
        """Cumulative season value on the last readout date."""
        if self.last_readout is None:
            return None
        return self.days.get(self.last_readout)

    @property
    def readout_increase(self) -> float | None:
        """Units added on the last readout date.

        After a season reset the value drops; the new value is then the increase.
        """
        current = self.readout_value
        if current is None or self.last_readout is None:
            return None
        earlier = [day for day in self.days if day < self.last_readout]
        if not earlier:
            return None
        previous = self.days[max(earlier)]
        return current - previous if current >= previous else current


@dataclass(slots=True)
class ResidentialUnitInfo:
    """A residential unit with its billing unit."""

    id: int
    billing_unit_id: int
    apartment_no: str | None
    model: str | None


@dataclass(slots=True)
class MijntedData:
    """Everything one coordinator update produces."""

    units: dict[int, ResidentialUnitInfo] = field(default_factory=dict)
    meters: dict[str, Meter] = field(default_factory=dict)


def daily_changes(
    days: dict[date, float], previous: float | None
) -> list[tuple[date, float, float]]:
    """Return (day, cumulative value, change) per day, oldest first.

    The change is the difference with the day before. A drop means a new heating season: the
    counter restarted at zero, so the new value is the change. The first day has change 0 when
    `previous` is unknown.
    """
    result: list[tuple[date, float, float]] = []
    for day in sorted(days):
        value = days[day]
        if previous is None:
            change = 0.0
        elif value >= previous:
            change = value - previous
        else:
            change = value
        result.append((day, value, change))
        previous = value
    return result
