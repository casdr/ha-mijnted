"""Problem sensors (outdated data, meters not reporting) and the cost estimate."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.mijnted.const import (
    CONF_PRICE_PER_UNIT,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

from .helpers import at, fill, refresh, setup_entry

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .fake_portal import FakePortal

pytestmark = [pytest.mark.usefixtures("amsterdam", "mock_client")]


def entity_state(hass: HomeAssistant, domain: str, unique_id: str) -> Any:
    """State of an entity, found by its unique ID; None when it does not exist."""
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    return hass.states.get(entity_id) if entity_id else None


async def test_data_outdated(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test the problem turns on once the newest readout is more than 4 days old."""
    at(freezer, "2026-11-15 10:00:00")
    fill(portal, date(2026, 11, 10), [10, 12, 17, 22])
    portal.last_sync = date(2026, 11, 13)
    await setup_entry(hass, config_entry)

    outdated = entity_state(hass, "binary_sensor", "unit_100001_data_outdated")
    assert outdated.state == "off"
    assert outdated.attributes["last_readout_date"] == "2026-11-13"

    at(freezer, "2026-11-18 10:00:00")
    await refresh(hass, config_entry)
    assert (
        entity_state(hass, "binary_sensor", "unit_100001_data_outdated").state == "on"
    )


async def test_data_outdated_without_readout(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
) -> None:
    """Test a unit without any readout counts as outdated."""
    at(freezer, "2026-11-15 10:00:00")
    await setup_entry(hass, config_entry)
    outdated = entity_state(hass, "binary_sensor", "unit_100001_data_outdated")
    assert outdated.state == "on"
    assert outdated.attributes["last_readout_date"] is None


async def test_not_reporting(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test meters with and without a value, and a deactivated meter."""
    at(freezer, "2026-11-15 10:00:00")
    first, second, third = portal.devices[:3]
    fill(portal, date(2026, 11, 10), [10, 12, 17, 22])
    portal.set_day(date(2026, 11, 13), 4, second)
    portal.set_day(date(2026, 11, 13), 7, third)
    portal.statuses[2] = replace(
        portal.statuses[2], deactivation_date="01-11-2026 00:00:00"
    )
    portal.last_sync = date(2026, 11, 13)
    await setup_entry(hass, config_entry)

    assert entity_state(hass, "binary_sensor", f"{first}_not_reporting").state == "off"
    assert entity_state(hass, "binary_sensor", f"{second}_not_reporting").state == (
        "off"
    )
    deactivated = entity_state(hass, "binary_sensor", f"{third}_not_reporting")
    assert deactivated.state == "on"
    assert deactivated.attributes["deactivation_date"] == "2026-11-01"
    silent = entity_state(hass, "binary_sensor", f"{portal.devices[3]}_not_reporting")
    assert silent.state == "on"
    assert silent.attributes["last_value_date"] is None


async def test_cost_only_with_a_price(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Test the estimate appears with a price and goes away when it is cleared."""
    at(freezer, "2026-11-15 10:00:00")
    fill(portal, date(2026, 11, 10), [10, 12, 17, 22])
    portal.set_day(date(2026, 11, 10), 5, portal.devices[1])
    portal.last_sync = date(2026, 11, 13)
    await setup_entry(hass, config_entry)
    assert entity_state(hass, "sensor", "unit_100001_season_cost") is None

    hass.config_entries.async_update_entry(
        config_entry,
        options={CONF_UPDATE_INTERVAL: 6, CONF_PRICE_PER_UNIT: 0.4321},
    )
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    cost = entity_state(hass, "sensor", "unit_100001_season_cost")
    # 22 + 5 units at 0.4321 each.
    assert float(cost.state) == 11.67
    assert cost.attributes["unit_of_measurement"] == hass.config.currency
    assert cost.attributes["season_total"] == 27
    assert cost.attributes["price_per_unit"] == 0.4321

    hass.config_entries.async_update_entry(
        config_entry, options={CONF_UPDATE_INTERVAL: 6, CONF_PRICE_PER_UNIT: 0}
    )
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, "unit_100001_season_cost"
        )
        is None
    )
