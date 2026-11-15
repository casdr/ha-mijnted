"""Heating seasons: find their boundaries in the data and compare with last year.

A season ends where the household's cumulative total drops back to about zero. The
portal's billing periods do not say when the meters reset (for one account the reset
was on 2 June, a day after the billing period started), so the boundaries come from
the values themselves. Nothing here uses Home Assistant.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    type TotalFetcher = Callable[[date], Awaitable[float | None]]

# Days between samples while looking for the start of the current season.
SCAN_STEP = 7
# How far back to look at most: two seasons and some slack.
MAX_SCAN_DAYS = 800
# Missing days tolerated inside the previous season before it is taken to start.
MAX_GAP_DAYS = 7
# Days a reset may take: meters do not always reset on the same day.
MAX_RESET_DAYS = 3
# A drop from below this total is not taken as a new season after the start was
# found. Early in a season a total of 2 falling to 1 halves, and would otherwise
# start a new backfill.
MIN_SEASON_TOTAL = 10


def is_reset(previous: float, value: float) -> bool:
    """Tell whether a total going from `previous` to `value` is a season reset.

    The total must fall to at most half. A replaced meter lowers the total a little;
    a new season brings it back to about zero.
    """
    return value < previous and value <= previous / 2


def is_new_season(previous: float, value: float) -> bool:
    """Tell whether a drop ends a season that counted at least `MIN_SEASON_TOTAL`."""
    return previous >= MIN_SEASON_TOTAL and is_reset(previous, value)


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


def shift_one_year(days: Mapping[date, float]) -> dict[date, float]:
    """Return the values moved forward one calendar year.

    29 February lands on 28 February and replaces that day's value, as it is the
    later of the two.
    """
    return {one_year_later(day): days[day] for day in sorted(days)}


def _days_to_json(days: Mapping[date, float]) -> dict[str, float]:
    return {day.isoformat(): value for day, value in sorted(days.items())}


def _days_from_json(data: Mapping[str, float]) -> dict[date, float]:
    return {date.fromisoformat(day): float(value) for day, value in data.items()}


@dataclass(slots=True)
class PreviousSeason:
    """The cumulative values per day during the previous season.

    `days` holds the household total, `meters` the value of each meter by device
    number. Seasons stored by version 0.2.0 have no `meters`.
    """

    start: date
    end: date
    days: dict[date, float]
    meters: dict[str, dict[date, float]] = field(default_factory=dict)

    def _values(self, device: str | None) -> dict[date, float] | None:
        return self.days if device is None else self.meters.get(device)

    def final_total(self, device: str | None = None) -> float | None:
        """Value at the end of the season: the household's, or one meter's."""
        values = self._values(device)
        return values[max(values)] if values else None

    def to_date(self, day: date, device: str | None = None) -> float | None:
        """Value up to and including `day` (a date in this season).

        The household's by default, or one meter's; None for a meter that was not
        there. Before the season started it is 0, after it ended the final value. A
        day without a value takes the newest earlier one.
        """
        values = self._values(device)
        if values is None:
            return None
        if day < self.start:
            return 0.0
        earlier = [d for d in values if d <= day]
        return values[max(earlier)] if earlier else 0.0

    def shifted(self) -> dict[date, float]:
        """Return the household totals moved forward one calendar year."""
        return shift_one_year(self.days)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": _days_to_json(self.days),
            "meters": {
                device: _days_to_json(days) for device, days in self.meters.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreviousSeason:
        """Restore from storage."""
        return cls(
            start=date.fromisoformat(data["start"]),
            end=date.fromisoformat(data["end"]),
            days=_days_from_json(data["days"]),
            meters={
                device: _days_from_json(days)
                for device, days in data.get("meters", {}).items()
            },
        )


@dataclass(slots=True)
class SeasonHistory:
    """Where the current season started, and the season before it if known.

    `checked_until` is the newest day the search looked at; a drop up to then is
    already accounted for. `current_days` holds the current season's totals as
    fetched; they go into the recorder once and are not stored with the rest.
    """

    current_start: date | None
    previous: PreviousSeason | None
    current_days: dict[date, float] = field(default_factory=dict, compare=False)
    checked_until: date | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage."""
        return {
            "current_start": _iso(self.current_start),
            "checked_until": _iso(self.checked_until),
            "previous": self.previous.as_dict() if self.previous else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeasonHistory:
        """Restore from storage."""
        previous = data.get("previous")
        return cls(
            current_start=_date(data.get("current_start")),
            previous=PreviousSeason.from_dict(previous) if previous else None,
            checked_until=_date(data.get("checked_until")),
        )


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _date(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def reset_after(totals: Mapping[date, float], after: date | None) -> date | None:
    """Return the newest day in `totals` on which a season started after `after`."""
    found = None
    days = sorted(totals)
    for earlier, day in itertools.pairwise(days):
        if (after is None or day > after) and is_new_season(
            totals[earlier], totals[day]
        ):
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


async def _reset_span(totals: _Totals, day: date, latest: date) -> tuple[date, date]:
    """Return the first and last day of the drop that `day` is part of.

    When the meters reset on different days, the total falls over a few days in a
    row. The season before ends the day before the first drop; the new season starts
    at the last, its lowest point.
    """
    first = day
    for _ in range(MAX_RESET_DAYS):
        # Is the day before `first` itself already lower than the day before it?
        before = await totals.get(first - timedelta(days=2))
        value = await totals.get(first - timedelta(days=1))
        if before is None or value is None or before <= value:
            break
        first -= timedelta(days=1)
    last = day
    for _ in range(MAX_RESET_DAYS):
        if last >= latest:
            break
        after = await totals.get(last + timedelta(days=1))
        value = await totals.get(last)
        if after is None or value is None or after >= value:
            break
        last += timedelta(days=1)
    return first, last


async def _fetch_previous(
    totals: _Totals, first_drop: date, earliest: date
) -> PreviousSeason | None:
    """Fetch the season before the reset day by day, back to its own reset."""
    end = first_drop - timedelta(days=1)
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
    """Find the start of the current season and fetch it and the previous season.

    `fetch` returns the household total of one day, or None without data. `latest` is
    the newest day with a real readout.
    """
    totals = _Totals(fetch)
    earliest = latest - timedelta(days=max_days)
    found = await _find_current_start(totals, latest, earliest)
    if found is None:
        return SeasonHistory(current_start=None, previous=None, checked_until=latest)
    first_drop, current_start = await _reset_span(totals, found, latest)
    previous = await _fetch_previous(totals, first_drop, earliest)
    current_days: dict[date, float] = {}
    day = current_start
    while day <= latest:
        if (value := await totals.get(day)) is not None:
            current_days[day] = value
        day += timedelta(days=1)
    return SeasonHistory(
        current_start=current_start,
        previous=previous,
        current_days=current_days,
        checked_until=latest,
    )
