"""Sensors for mijnTED heat cost allocators."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, ROOMS_EN, ROOMS_NL, UNIT_UNITS
from .coordinator import MijntedConfigEntry, MijntedCoordinator
from .models import Meter, ResidentialUnitInfo, meter_name
from .statistics import statistic_id

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


def unit_device_identifier(unit_id: int) -> tuple[str, str]:
    """Device registry identifier of a residential unit."""
    return (DOMAIN, f"unit_{unit_id}")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MijntedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors, adding meters that appear later as well."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_meters() -> None:
        device_registry = dr.async_get(hass)
        unit_device_ids: dict[int, str] = {}
        for unit in coordinator.data.units.values():
            unit_device_ids[unit.id] = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={unit_device_identifier(unit.id)},
                manufacturer=MANUFACTURER,
                model=unit.model,
                name=_unit_name(unit),
                entry_type=dr.DeviceEntryType.SERVICE,
            ).id
        new = [number for number in coordinator.data.meters if number not in known]
        known.update(new)
        async_add_entities(
            MijntedSensor(
                coordinator,
                number,
                description,
                unit_device_ids.get(coordinator.data.meters[number].residential_unit_id),
            )
            for number in new
            for description in SENSORS
        )

    add_new_meters()
    entry.async_on_unload(coordinator.async_add_listener(add_new_meters))


def _unit_name(unit: ResidentialUnitInfo) -> str:
    return f"mijnTED {unit.apartment_no}" if unit.apartment_no else f"mijnTED {unit.id}"


class MijntedSensor(CoordinatorEntity[MijntedCoordinator], SensorEntity):
    """A value of one heat cost allocator."""

    _attr_has_entity_name = True
    entity_description: MijntedSensorDescription

    def __init__(
        self,
        coordinator: MijntedCoordinator,
        device_number: str,
        description: MijntedSensorDescription,
        unit_device_id: str | None,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_number = device_number
        self._attr_unique_id = f"{device_number}_{description.key}"
        meter = coordinator.data.meters[device_number]
        language = coordinator.hass.config.language
        rooms = ROOMS_NL if language.lower().startswith("nl") else ROOMS_EN
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_number)},
            name=meter_name(device_number, meter.room_code, language),
            manufacturer=MANUFACTURER,
            model=meter.model,
            serial_number=device_number,
            suggested_area=rooms.get(meter.room_code or "", meter.room_code),
        )
        if unit_device_id:
            self._attr_device_info["via_device_id"] = unit_device_id

    @property
    def _meter(self) -> Meter | None:
        return self.coordinator.data.meters.get(self._device_number)

    @property
    def available(self) -> bool:
        """Available while the last update succeeded and still lists this meter."""
        return super().available and self._meter is not None

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        meter = self._meter
        return self.entity_description.value_fn(meter) if meter else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the readout date and related values."""
        meter = self._meter
        return self.entity_description.attrs_fn(meter) if meter else None
