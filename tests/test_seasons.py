"""Tests for season boundaries, the one-year shift and the to-date value."""

from __future__ import annotations

from datetime import date, timedelta

from custom_components.mijnted.seasons import (
    PreviousSeason,
    SeasonHistory,
    async_find_seasons,
    is_reset,
    one_year_earlier,
    one_year_later,
    reset_after,
)


def ramp(first: date, last: date, final: float) -> dict[date, float]:
    """Count up evenly from 0 on `first` to `final` on `last`."""
    days = (last - first).days
    return {
        first + timedelta(days=offset): round(final * offset / days)
        for offset in range(days + 1)
    }


class Portal:
    """Household totals per day, counting how many days were asked."""

    def __init__(self, totals: dict[date, float]) -> None:
        """Serve the given totals."""
        self.totals = totals
        self.asked: list[date] = []

    async def fetch(self, day: date) -> float | None:
        """Return the total of a day, None without data."""
        self.asked.append(day)
        return self.totals.get(day)


# Three seasons, each reset on 2 June, as for the account the portal capture is from.
SEASON_BEFORE = ramp(date(2024, 6, 2), date(2025, 6, 1), 400)
PREVIOUS = ramp(date(2025, 6, 2), date(2026, 6, 1), 489)
CURRENT = ramp(date(2026, 6, 2), date(2026, 11, 13), 22)
LATEST = date(2026, 11, 13)


async def test_boundaries_from_a_reset_in_june() -> None:
    """Test the seasons are found from the drops on 2 June, not from a calendar."""
    portal = Portal(SEASON_BEFORE | PREVIOUS | CURRENT)
    history = await async_find_seasons(portal.fetch, LATEST)

    assert history.current_start == date(2026, 6, 2)
    previous = history.previous
    assert previous is not None
    assert previous.start == date(2025, 6, 2)
    assert previous.end == date(2026, 6, 1)
    assert previous.final_total == 489
    assert previous.days == PREVIOUS
    assert history.current_days == CURRENT
    # Every day of both seasons once, plus the day before them that ends the search.
    assert len(portal.asked) == len(set(portal.asked))
    assert set(portal.asked) == set(PREVIOUS) | set(CURRENT) | {date(2025, 6, 1)}


async def test_replaced_meter_is_not_a_new_season() -> None:
    """Test a small drop, such as a replaced meter, does not end the season."""
    previous = dict(PREVIOUS)
    for day in previous:
        if day >= date(2026, 1, 10):
            previous[day] -= 60
    portal = Portal(SEASON_BEFORE | previous | CURRENT)
    history = await async_find_seasons(portal.fetch, LATEST)
    assert history.previous is not None
    assert history.previous.start == date(2025, 6, 2)


async def test_new_account_has_no_previous_season() -> None:
    """Test data that starts mid-season gives a start but no previous season."""
    portal = Portal(ramp(date(2026, 8, 1), LATEST, 22))
    history = await async_find_seasons(portal.fetch, LATEST)
    assert history.current_start == date(2026, 8, 1)
    assert history.previous is None


async def test_no_data() -> None:
    """Test a unit without any values."""
    history = await async_find_seasons(Portal({}).fetch, LATEST)
    assert history == SeasonHistory(current_start=None, previous=None)


def test_one_year_shift_maps_29_february() -> None:
    """Test 29 February moves to 28 February, which then holds its later value."""
    assert one_year_later(date(2028, 2, 29)) == date(2029, 2, 28)
    assert one_year_earlier(date(2028, 2, 29)) == date(2027, 2, 28)
    assert one_year_later(date(2027, 2, 28)) == date(2028, 2, 28)
    assert one_year_later(date(2025, 11, 15)) == date(2026, 11, 15)

    season = PreviousSeason(
        start=date(2028, 2, 28),
        end=date(2028, 3, 1),
        days={date(2028, 2, 28): 10, date(2028, 2, 29): 12, date(2028, 3, 1): 13},
    )
    assert season.shifted() == {date(2029, 2, 28): 12, date(2029, 3, 1): 13}


def test_to_date() -> None:
    """Test the to-date value before, during, in a gap and after the season."""
    days = dict(PREVIOUS)
    del days[date(2025, 11, 15)]
    season = PreviousSeason(start=date(2025, 6, 2), end=date(2026, 6, 1), days=days)

    assert season.to_date(date(2025, 6, 1)) == 0
    assert season.to_date(date(2025, 11, 14)) == PREVIOUS[date(2025, 11, 14)]
    assert season.to_date(date(2025, 11, 15)) == PREVIOUS[date(2025, 11, 14)]
    assert season.to_date(date(2026, 6, 1)) == 489
    assert season.to_date(date(2026, 6, 20)) == 489


def test_reset_detection() -> None:
    """Test what counts as a reset, and finding the newest one after a date."""
    assert is_reset(489, 0)
    assert is_reset(489, 3)
    assert not is_reset(489, 430)
    assert not is_reset(0, 0)

    window = {date(2027, 5, 31): 300, date(2027, 6, 1): 301, date(2027, 6, 2): 0}
    assert reset_after(window, date(2026, 6, 2)) == date(2027, 6, 2)
    assert reset_after(window, date(2027, 6, 2)) is None


def test_storage_round_trip() -> None:
    """Test a season history survives being stored."""
    history = SeasonHistory(
        current_start=date(2026, 6, 2),
        previous=PreviousSeason(date(2025, 6, 2), date(2026, 6, 1), dict(PREVIOUS)),
    )
    assert SeasonHistory.from_dict(history.as_dict()) == history
