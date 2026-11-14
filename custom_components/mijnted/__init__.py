"""The mijnTED integration: Techem heat cost allocator readings from mijnted.nl."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .api import MijntedClient
from .const import MANUFACTURER
from .coordinator import MijntedConfigEntry, MijntedCoordinator
from .entity import unit_device_identifier

if TYPE_CHECKING:
    from .models import ResidentialUnitInfo

PLATFORMS: list[Platform] = [Platform.SENSOR]


def create_client(hass: HomeAssistant, email: str, password: str) -> MijntedClient:
    """Create a client with its own cookie jar, detached when the entry unloads."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    return MijntedClient(session, email, password, dt_util.get_default_time_zone())


async def async_setup_entry(hass: HomeAssistant, entry: MijntedConfigEntry) -> bool:
    """Set up mijnTED from a config entry."""
    client = create_client(hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = MijntedCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def register_units() -> None:
        registry = dr.async_get(hass)
        for unit in coordinator.data.units.values():
            registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={unit_device_identifier(unit.id)},
                manufacturer=MANUFACTURER,
                model=unit.model,
                name=_unit_name(unit),
                entry_type=dr.DeviceEntryType.SERVICE,
            )

    # Units first: the meters' devices refer to them.
    register_units()
    entry.async_on_unload(coordinator.async_add_listener(register_units))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _unit_name(unit: ResidentialUnitInfo) -> str:
    return f"mijnTED {unit.apartment_no or unit.id}"


async def async_unload_entry(hass: HomeAssistant, entry: MijntedConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
