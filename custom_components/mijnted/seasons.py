"""Heating seasons: find their boundaries in the data and compare with last year.

A season ends where the household's cumulative total drops back to about zero. The
portal's billing periods do not say when the meters reset (for one account the reset
was on 2 June, a day after the billing period started), so the boundaries come from
the values themselves. Nothing here uses Home Assistant.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    type TotalFetcher = Callable[[date], Awaitable[float | None]]

# Days between samples while looking for the start of the current season.
SCAN_STEP = 7
# How far back to look at most: two seasons and some slack.
MAX_SCAN_DAYS = 800
# Missing days tolerated inside the previous season before it is taken to start.
MAX_GAP_DAYS = 7


def is_reset(previous: float, value: float) -> bool:
    """Tell whether a total going from `previous` to `value` is a season reset.

    The total must fall to at most half. A replaced meter lowers the total a little;
    a new season brings it back to about zero.
    """
    return value < previous and value <= previous / 2


def one_year_later(day: date) -> date:
    """Return the same date a year later; 29 February becomes 28 February."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return date(day.year + 1, 2, 28)


def one_year_earlier(day: date) -> date:
    """Return the same date a year earlier; 29 February becomes 28 February."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return date(day.year - 1, 2, 28)


@dataclass(slots=True)
class PreviousSeason:
    """The household's cumulative total per day during the previous season."""

    start: date
    end: date
    days: dict[date, float]

    @property
    def final_total(self) -> float:
        """Total at the end of the season."""
        return self.days[max(self.days)]

    def to_date(self, day: date) -> float:
        """Total of this season up to and including `day` (a date in this season).

        Before the season started it is 0, after it ended the final total. A day
        without a value takes the newest earlier one.
        """
        if day < self.start:
            return 0.0
        earlier = [d for d in self.days if d <= day]
        return self.days[max(earlier)] if earlier else 0.0

    def shifted(self) -> dict[date, float]:
        """Return the totals moved forward one calendar year.

        29 February lands on 28 February and replaces that day's value, as it is the
        later of the two.
        """
        return {one_year_later(day): self.days[day] for day in sorted(self.days)}

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": {
                day.isoformat(): value for day, value in sorted(self.days.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreviousSeason:
        """Restore from storage."""
        return cls(
            start=date.fromisoformat(data["start"]),
            end=date.fromisoformat(data["end"]),
            days={date.fromisoformat(day): float(v) for day, v in data["days"].items()},
        )


@dataclass(slots=True)
class SeasonHistory:
    """Where the current season started, and the season before it if known."""

    current_start: date | None
    previous: PreviousSeason | None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage."""
        return {
            "current_start": self.current_start.isoformat()
            if self.current_start
            else None,
            "previous": self.previous.as_dict() if self.previous else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeasonHistory:
        """Restore from storage."""
        start = data.get("current_start")
        previous = data.get("previous")
        return cls(
            current_start=date.fromisoformat(start) if start else None,
            previous=PreviousSeason.from_dict(previous) if previous else None,
        )


def reset_after(totals: dict[date, float], after: date | None) -> date | None:
    """Return the newest day in `totals` on which a season started after `after`."""
    found = None
    days = sorted(totals)
    for earlier, day in itertools.pairwise(days):
        if (after is None or day > after) and is_reset(totals[earlier], totals[day]):
            found = day
    return found


class _Totals:
    """Fetch household totals at most once per day."""

    def __init__(self, fetch: TotalFetcher) -> None:
        """Wrap the fetch function."""
        self._fetch = fetch
        self._cache: dict[date, float | None] = {}

    async def get(self, day: date) -> float | None:
        """Return the total of a day, or None when the portal has no value."""
        if day not in self._cache:
            self._cache[day] = await self._fetch(day)
        return self._cache[day]


async def _find_current_start(
    totals: _Totals, latest: date, earliest: date
) -> date | None:
    """Step back a week at a time to the reset, then find its day."""
    later = latest
    later_value = await totals.get(later)
    if later_value is None:
        return None
    while (earlier := later - timedelta(days=SCAN_STEP)) >= earliest:
        earlier_value = await totals.get(earlier)
        if earlier_value is None or is_reset(earlier_value, later_value):
            break
        later, later_value = earlier, earlier_value
    else:
        return None
    # The reset (or the first day with data) is in (earlier, later].
    previous_value = earlier_value
    day = earlier + timedelta(days=1)
    while day <= later:
        value = await totals.get(day)
        if value is not None:
            if previous_value is None or is_reset(previous_value, value):
                return day
            previous_value = value
        day += timedelta(days=1)
    return later


async def _fetch_previous(
    totals: _Totals, current_start: date, earliest: date
) -> PreviousSeason | None:
    """Fetch the season before `current_start` day by day, back to its reset."""
    end = current_start - timedelta(days=1)
    days: dict[date, float] = {}
    day, newer_value, missing = end, None, 0
    while day >= earliest and missing <= MAX_GAP_DAYS:
        value = await totals.get(day)
        if value is None:
            missing += 1
        elif newer_value is not None and is_reset(value, newer_value):
            break
        else:
            days[day] = value
            newer_value, missing = value, 0
        day -= timedelta(days=1)
    if not days:
        return None
    return PreviousSeason(start=min(days), end=end, days=days)


async def async_find_seasons(
    fetch: TotalFetcher, latest: date, max_days: int = MAX_SCAN_DAYS
) -> SeasonHistory:
    """Find the start of the current season and fetch the previous season.

    `fetch` returns the household total of one day, or None without data. `latest` is
    the newest day with a real readout.
    """
    totals = _Totals(fetch)
    earliest = latest - timedelta(days=max_days)
    current_start = await _find_current_start(totals, latest, earliest)
    if current_start is None:
        return SeasonHistory(current_start=None, previous=None)
    previous = await _fetch_previous(totals, current_start, earliest)
    return SeasonHistory(current_start=current_start, previous=previous)
