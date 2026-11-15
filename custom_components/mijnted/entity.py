"""Base entity: one device per heat cost allocator, identified by its device number.

Meters hang under the device of their residential unit (apartment), which
`__init__.py` registers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, ROOMS_EN, ROOMS_NL
from .coordinator import MijntedCoordinator
from .models import meter_name

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import MijntedConfigEntry
    from .models import Meter


def unit_device_identifier(unit_id: int) -> tuple[str, str]:
    """Device registry identifier of a residential unit."""
    return (DOMAIN, f"unit_{unit_id}")


class MijntedMeterEntity(CoordinatorEntity[MijntedCoordinator]):
    """An entity of one heat cost allocator."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: MijntedCoordinator, device_number: str, key: str
    ) -> None:
        """Initialize the entity.

        Args:
        ----
            coordinator: The coordinator.
            device_number: The meter's device number, printed on the allocator.
            key: Added to the unique ID, to tell the meter's entities apart.

        """
        super().__init__(coordinator)
        self._device_number = device_number
        self._attr_unique_id = f"{device_number}_{key}"
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
        unit_device = dr.async_get(coordinator.hass).async_get_device_by_identifier(
            unit_device_identifier(meter.residential_unit_id),
            coordinator.config_entry.entry_id,
        )
        if unit_device:
            self._attr_device_info["via_device_id"] = unit_device.id

    @property
    def meter(self) -> Meter | None:
        """The meter as last read, None when the portal no longer lists it."""
        return self.coordinator.data.meters.get(self._device_number)

    @property
    def available(self) -> bool:
        """Available while the last update succeeded and still lists this meter."""
        return super().available and self.meter is not None


class MijntedUnitEntity(CoordinatorEntity[MijntedCoordinator]):
    """An entity of a residential unit (apartment), on the unit's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MijntedCoordinator, unit_id: int, key: str) -> None:
        """Initialize the entity.

        Args:
        ----
            coordinator: The coordinator.
            unit_id: The residential unit's id in the portal.
            key: Added to the unique ID, to tell the unit's entities apart.

        """
        super().__init__(coordinator)
        self._unit_id = unit_id
        self._attr_unique_id = f"unit_{unit_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={unit_device_identifier(unit_id)}
        )

    @property
    def available(self) -> bool:
        """Available while the last update succeeded and still lists this unit."""
        return super().available and self._unit_id in self.coordinator.data.units


def async_add_unit_entities(
    entry: MijntedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    create: Callable[[MijntedCoordinator, int], Iterable[Entity]],
) -> None:
    """Add entities for every residential unit now, and for units that appear later."""
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new_units() -> None:
        new = [unit_id for unit_id in coordinator.data.units if unit_id not in known]
        known.update(new)
        async_add_entities(
            entity for unit_id in new for entity in create(coordinator, unit_id)
        )

    add_new_units()
    entry.async_on_unload(coordinator.async_add_listener(add_new_units))


def async_add_meter_entities(
    entry: MijntedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    create: Callable[[MijntedCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every meter now, and for meters that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_meters() -> None:
        new = [number for number in coordinator.data.meters if number not in known]
        known.update(new)
        async_add_entities(
            entity for number in new for entity in create(coordinator, number)
        )

    add_new_meters()
    entry.async_on_unload(coordinator.async_add_listener(add_new_meters))
