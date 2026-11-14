"""The mijnTED integration: Techem heat cost allocator readings from mijnted.nl."""

from __future__ import annotations

import aiohttp

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import MijntedClient
from .coordinator import MijntedConfigEntry, MijntedCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


def create_client(hass: HomeAssistant, email: str, password: str) -> MijntedClient:
    """Create a client with its own cookie jar, detached when the entry unloads."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    return MijntedClient(session, email, password, hass.config.time_zone_info)


async def async_setup_entry(hass: HomeAssistant, entry: MijntedConfigEntry) -> bool:
    """Set up mijnTED from a config entry."""
    client = create_client(hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = MijntedCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MijntedConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
