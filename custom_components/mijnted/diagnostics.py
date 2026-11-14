"""Diagnostics for mijnTED, with credentials, tokens and ids redacted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import MijntedConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant,
    entry: MijntedConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Residential units and meters are numbered in order instead of showing their ids.
    """
    coordinator = entry.runtime_data
    data = coordinator.data
    unit_alias = {
        unit_id: f"unit_{index}" for index, unit_id in enumerate(data.units, 1)
    }
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "signed_in": coordinator.client.account_id is not None,
        "last_update_success": coordinator.last_update_success,
        "update_interval": str(coordinator.update_interval),
        "units": [
            {
                "unit": unit_alias[unit.id],
                "billing_unit_id": REDACTED,
                "apartment_no": REDACTED,
                "model": unit.model,
            }
            for unit in data.units.values()
        ],
        "meters": [
            {
                "meter": f"meter_{index}",
                "unit": unit_alias.get(meter.residential_unit_id, REDACTED),
                "room_code": meter.room_code,
                "unit_of_measure": meter.unit_of_measure,
                "model": meter.model,
                "last_readout": meter.last_readout.isoformat()
                if meter.last_readout
                else None,
                "days": {
                    day.isoformat(): value for day, value in sorted(meter.days.items())
                },
            }
            for index, meter in enumerate(data.meters.values(), 1)
        ],
    }
