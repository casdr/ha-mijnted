"""Data model for mijnTED readings, independent of Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .const import ROOMS_EN, ROOMS_NL
from .seasons import is_reset

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date


def meter_name(device_number: str, room_code: str | None, language: str) -> str:
    """Display name for a meter: room name plus device number, as rooms often repeat."""
    if not room_code:
        return device_number
    rooms = ROOMS_NL if language.lower().startswith("nl") else ROOMS_EN
    return f"{rooms.get(room_code, room_code)} {device_number}"


@dataclass(slots=True)
class Meter:
    """One heat cost allocator and its per-day cumulative values.

    `days` holds only days up to and including the last readout date. Days after it
    repeat the last value in the portal and are not real readings.
    """

    device_number: str
    residential_unit_id: int
    room_code: str | None
    unit_of_measure: str | None
    model: str | None
    last_readout: date | None
    days: dict[date, float] = field(default_factory=dict)
    # Set when the portal lists the meter as deactivated.
    deactivation_date: date | None = None

    @property
    def reporting(self) -> bool:
        """Whether the meter is active and has a value on the last readout date."""
        return self.deactivation_date is None and self.readout_value is not None

    @property
    def readout_value(self) -> float | None:
        """Cumulative season value on the last readout date."""
        if self.last_readout is None:
            return None
        return self.days.get(self.last_readout)

    @property
    def readout_increase(self) -> float | None:
        """Units added on the last readout date, by the rules of `daily_changes`."""
        current = self.readout_value
        if current is None or self.last_readout is None:
            return None
        earlier = [day for day in self.days if day < self.last_readout]
        if not earlier:
            return None
        return daily_changes({self.last_readout: current}, self.days[max(earlier)])[0][
            2
        ]


@dataclass(slots=True)
class ResidentialUnitInfo:
    """A residential unit with its billing unit."""

    id: int
    billing_unit_id: int
    apartment_no: str | None
    model: str | None
    # Newest readout date of the unit's meters.
    last_readout: date | None = None


@dataclass(slots=True)
class MijntedData:
    """Everything one coordinator update produces."""

    units: dict[int, ResidentialUnitInfo] = field(default_factory=dict)
    meters: dict[str, Meter] = field(default_factory=dict)
    # Household total per day (all meters added up) up to the last readout, per unit.
    totals: dict[int, dict[date, float]] = field(default_factory=dict)


def daily_changes(
    days: Mapping[date, float], previous: float | None
) -> list[tuple[date, float, float]]:
    """Return (day, cumulative value, change) per day, oldest first.

    The change is the rise above the highest value so far. A drop to about zero
    (`is_reset`) is a new heating season: the counter restarted, so the new value is
    the change. A smaller drop, such as a correction or a replaced meter, adds
    nothing until the value is back above where it was. The first day has change 0
    when `previous` is unknown.
    """
    result: list[tuple[date, float, float]] = []
    high = previous
    for day in sorted(days):
        value = days[day]
        if high is None:
            change, high = 0.0, value
        elif value >= high:
            change, high = value - high, value
        elif is_reset(high, value):
            change, high = value, value
        else:
            change = 0.0
        result.append((day, value, change))
    return result


def household_changes(
    meters: Iterable[Mapping[date, float]], days: Iterable[date]
) -> dict[date, float]:
    """Return the household's change per day as the sum of each meter's change.

    Each meter's change is taken between consecutive household `days`, by the rules
    of `daily_changes`. Meters that reset on different days then add only their new
    units, where the difference of household totals would count the meters not yet
    reset as new units too. The first day has change 0.
    """
    wanted = set(days)
    result = dict.fromkeys(wanted, 0.0)
    for values in meters:
        kept = {day: value for day, value in values.items() if day in wanted}
        for day, _, change in daily_changes(kept, None):
            result[day] += change
    return result
