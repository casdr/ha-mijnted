"""Helpers shared by the tests that set up the integration."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mijnted.const import DOMAIN

from .fake_portal import FakePortal


def at(freezer: FrozenDateTimeFactory, moment: str) -> None:
    """Move the clock to a local Amsterdam time."""
    freezer.move_to(f"{moment}+01:00")


def fill(portal: FakePortal, first: date, values: list[float]) -> None:
    for offset, value in enumerate(values):
        portal.set_day(first + timedelta(days=offset), value)


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def state(hass: HomeAssistant, device: str, key: str) -> Any:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{device}_{key}"
    )
    assert entity_id
    return hass.states.get(entity_id)


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
