"""Problem sensors: outdated portal data and meters that do not report."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .const import STALE_AFTER
from .entity import (
    MijntedMeterEntity,
    MijntedUnitEntity,
    async_add_meter_entities,
    async_add_unit_entities,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import MijntedConfigEntry, MijntedCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: MijntedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the problem sensors of every residential unit and meter."""
    async_add_unit_entities(
        entry,
        async_add_entities,
        lambda coordinator, unit_id: [DataOutdatedSensor(coordinator, unit_id)],
    )
    async_add_meter_entities(
        entry,
        async_add_entities,
        lambda coordinator, number: [NotReportingSensor(coordinator, number)],
    )


class DataOutdatedSensor(MijntedUnitEntity, BinarySensorEntity):
    """On when the unit's newest readout is older than a few days.

    The portal is normally a day or two behind. Much older data means the readings
    stopped arriving, for example after a problem with the building's gateway.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "data_outdated"

    def __init__(self, coordinator: MijntedCoordinator, unit_id: int) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, unit_id, "data_outdated")

    @property
    def is_on(self) -> bool:
        """Return whether the newest readout is too old, or missing."""
        unit = self.coordinator.data.units[self._unit_id]
        if unit.last_readout is None:
            return True
        return dt_util.now().date() - unit.last_readout > STALE_AFTER

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the newest readout date."""
        unit = self.coordinator.data.units[self._unit_id]
        return {
            "last_readout_date": unit.last_readout.isoformat()
            if unit.last_readout
            else None
        }


class NotReportingSensor(MijntedMeterEntity, BinarySensorEntity):
    """On when a meter is deactivated or has no value on the last readout date.

    The portal shows no error or battery state of a meter, so this is what it can
    tell: a meter it lists without a reading.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "not_reporting"

    def __init__(self, coordinator: MijntedCoordinator, device_number: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_number, "not_reporting")

    @property
    def is_on(self) -> bool | None:
        """Return whether the meter is not reporting."""
        meter = self.meter
        return None if meter is None else not meter.reporting

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the newest day with a value and the deactivation date."""
        meter = self.meter
        if meter is None:
            return None
        return {
            "last_value_date": max(meter.days).isoformat() if meter.days else None,
            "deactivation_date": meter.deactivation_date.isoformat()
            if meter.deactivation_date
            else None,
        }
