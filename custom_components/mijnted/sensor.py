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
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import UNIT_UNITS
from .entity import (
    MijntedMeterEntity,
    MijntedUnitEntity,
    async_add_meter_entities,
    async_add_unit_entities,
)
from .seasons import one_year_earlier, one_year_later
from .statistics import statistic_id

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import MijntedConfigEntry, MijntedCoordinator
    from .models import Meter
    from .seasons import PreviousSeason

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
    """Set up the sensors of every meter and every residential unit."""
    async_add_meter_entities(
        entry,
        async_add_entities,
        lambda coordinator, number: [
            MijntedSensor(coordinator, number, description) for description in SENSORS
        ],
    )
    async_add_unit_entities(
        entry,
        async_add_entities,
        lambda coordinator, unit_id: [
            PreviousSeasonToDateSensor(coordinator, unit_id),
            PreviousSeasonTotalSensor(coordinator, unit_id),
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


class _PreviousSeasonSensor(MijntedUnitEntity, SensorEntity):
    """A value of the residential unit's previous heating season."""

    _attr_native_unit_of_measurement = UNIT_UNITS
    _attr_suggested_display_precision = 0

    _key: str

    def __init__(self, coordinator: MijntedCoordinator, unit_id: int) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, unit_id, self._key)
        self._attr_translation_key = self._key

    @property
    def previous(self) -> PreviousSeason | None:
        """The previous season, None while it is being fetched or unknown."""
        return self.coordinator.seasons.previous(self._unit_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the previous season's first and last day."""
        previous = self.previous
        if previous is None:
            return None
        return {
            "season_start": previous.start.isoformat(),
            "season_end": previous.end.isoformat(),
        }


class PreviousSeasonToDateSensor(_PreviousSeasonSensor):
    """Previous season's total on today's date one year earlier.

    The value changes at midnight, so the sensor writes its state then as well.
    """

    _key = "previous_season_to_date"
    _attr_state_class = SensorStateClass.TOTAL

    async def async_added_to_hass(self) -> None:
        """Write the state again just after midnight."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_new_day, hour=0, minute=0, second=5
            )
        )

    @callback
    def _async_new_day(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        """Return the previous season's total on this date last year."""
        previous = self.previous
        if previous is None:
            return None
        return previous.to_date(one_year_earlier(dt_util.now().date()))

    @property
    def last_reset(self) -> datetime | None:
        """Return when the previous season's counting started, a year later."""
        previous = self.previous
        if previous is None:
            return None
        return dt_util.start_of_local_day(one_year_later(previous.start))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the compared date and the previous season's first and last day."""
        attributes = super().extra_state_attributes
        if attributes is not None:
            attributes["comparison_date"] = one_year_earlier(
                dt_util.now().date()
            ).isoformat()
        return attributes


class PreviousSeasonTotalSensor(_PreviousSeasonSensor):
    """Previous season's final total."""

    _key = "previous_season_total"

    @property
    def native_value(self) -> float | None:
        """Return the previous season's final total."""
        previous = self.previous
        return previous.final_total if previous else None
