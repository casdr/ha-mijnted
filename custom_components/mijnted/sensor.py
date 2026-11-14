"""Sensors for mijnTED heat cost allocators."""

# Home Assistant builds entity descriptions with a metaclass pylint cannot follow.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from .const import UNIT_UNITS
from .entity import MijntedMeterEntity, async_add_meter_entities
from .statistics import statistic_id

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import MijntedConfigEntry, MijntedCoordinator
    from .models import Meter

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MijntedSensorDescription(SensorEntityDescription):
    """Describes a mijnTED meter sensor."""

    value_fn: Callable[[Meter], float | None]
    attrs_fn: Callable[[Meter], dict[str, Any]]


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


SENSORS: tuple[MijntedSensorDescription, ...] = (
    MijntedSensorDescription(
        key="cumulative",
        translation_key="cumulative",
        native_unit_of_measurement=UNIT_UNITS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda meter: meter.readout_value,
        attrs_fn=lambda meter: {
            "last_readout_date": _iso(meter.last_readout),
            "statistic_id": statistic_id(meter.device_number),
        },
    ),
    MijntedSensorDescription(
        key="last_readout",
        translation_key="last_readout",
        native_unit_of_measurement=UNIT_UNITS,
        suggested_display_precision=0,
        value_fn=lambda meter: meter.readout_increase,
        attrs_fn=lambda meter: {
            "readout_date": _iso(meter.last_readout),
            "cumulative": meter.readout_value,
        },
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: MijntedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of every meter."""
    async_add_meter_entities(
        entry,
        async_add_entities,
        lambda coordinator, number: [
            MijntedSensor(coordinator, number, description) for description in SENSORS
        ],
    )


class MijntedSensor(MijntedMeterEntity, SensorEntity):
    """A value of one heat cost allocator."""

    entity_description: MijntedSensorDescription

    def __init__(
        self,
        coordinator: MijntedCoordinator,
        device_number: str,
        description: MijntedSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_number, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        meter = self.meter
        return self.entity_description.value_fn(meter) if meter else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the readout date and related values."""
        meter = self.meter
        return self.entity_description.attrs_fn(meter) if meter else None
