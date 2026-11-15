"""Sensors for mijnTED heat cost allocators."""

# Home Assistant builds entity descriptions with a metaclass pylint cannot follow.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_PRICE_PER_UNIT, DOMAIN, UNIT_UNITS
from .entity import (
    MijntedMeterEntity,
    MijntedUnitEntity,
    async_add_meter_entities,
    async_add_unit_entities,
)
from .seasons import one_year_earlier, one_year_later
from .statistics import meter_previous_season_statistic_id, statistic_id

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
    hass: HomeAssistant,
    entry: MijntedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of every meter and every residential unit."""
    price = float(entry.options.get(CONF_PRICE_PER_UNIT) or 0)
    if not price:
        _remove_cost_sensors(hass, entry)
    async_add_meter_entities(
        entry,
        async_add_entities,
        lambda coordinator, number: [
            *(
                MijntedSensor(coordinator, number, description)
                for description in SENSORS
            ),
            MeterPreviousSeasonToDateSensor(coordinator, number),
        ],
    )

    def unit_sensors(
        coordinator: MijntedCoordinator, unit_id: int
    ) -> list[SensorEntity]:
        sensors: list[SensorEntity] = [
            PreviousSeasonToDateSensor(coordinator, unit_id),
            PreviousSeasonTotalSensor(coordinator, unit_id),
        ]
        if price:
            sensors.append(SeasonCostSensor(coordinator, unit_id, price))
        return sensors

    async_add_unit_entities(entry, async_add_entities, unit_sensors)


def _remove_cost_sensors(hass: HomeAssistant, entry: MijntedConfigEntry) -> None:
    """Remove the cost estimate after its price was cleared in the options."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform == DOMAIN and entity.unique_id.endswith(
            f"_{SeasonCostSensor.key}"
        ):
            registry.async_remove(entity.entity_id)


class _WriteAtMidnight(Entity):
    """Writes its state just after midnight, for values that move with the date."""

    async def async_added_to_hass(self) -> None:
        """Start writing the state at midnight."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_new_day, hour=0, minute=0, second=5
            )
        )

    @callback
    def _async_new_day(self, _now: datetime) -> None:
        self.async_write_ha_state()


def _comparison_date() -> date:
    """Today's date one year earlier: the day the previous season is compared on."""
    return one_year_earlier(dt_util.now().date())


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


class PreviousSeasonToDateSensor(_WriteAtMidnight, _PreviousSeasonSensor):
    """Previous season's total on today's date one year earlier.

    The value changes at midnight, so the sensor writes its state then as well.
    """

    _key = "previous_season_to_date"
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> float | None:
        """Return the previous season's total on this date last year."""
        previous = self.previous
        if previous is None:
            return None
        return previous.to_date(_comparison_date())

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
            attributes["comparison_date"] = _comparison_date().isoformat()
        return attributes


class PreviousSeasonTotalSensor(_PreviousSeasonSensor):
    """Previous season's final total."""

    _key = "previous_season_total"

    @property
    def native_value(self) -> float | None:
        """Return the previous season's final total."""
        previous = self.previous
        return previous.final_total() if previous else None


class MeterPreviousSeasonToDateSensor(
    _WriteAtMidnight, MijntedMeterEntity, SensorEntity
):
    """A meter's previous-season value on today's date one year earlier.

    Unknown while the previous season is being fetched, and for a meter that was
    not there last season.
    """

    _attr_native_unit_of_measurement = UNIT_UNITS
    _attr_suggested_display_precision = 0
    _attr_state_class = SensorStateClass.TOTAL
    _attr_translation_key = "previous_season_to_date"

    def __init__(self, coordinator: MijntedCoordinator, device_number: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_number, "previous_season_to_date")

    @property
    def previous(self) -> PreviousSeason | None:
        """The previous season of the meter's residential unit."""
        meter = self.meter
        if meter is None:
            return None
        return self.coordinator.seasons.previous(meter.residential_unit_id)

    @property
    def native_value(self) -> float | None:
        """Return the meter's value on this date last season."""
        previous = self.previous
        if previous is None:
            return None
        return previous.to_date(_comparison_date(), self._device_number)

    @property
    def last_reset(self) -> datetime | None:
        """Return when the previous season's counting started, a year later."""
        previous = self.previous
        if previous is None:
            return None
        return dt_util.start_of_local_day(one_year_later(previous.start))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the compared date, the season and the statistic id."""
        previous = self.previous
        if previous is None:
            return None
        return {
            "comparison_date": _comparison_date().isoformat(),
            "season_start": previous.start.isoformat(),
            "season_end": previous.end.isoformat(),
            "statistic_id": meter_previous_season_statistic_id(self._device_number),
        }


class SeasonCostSensor(MijntedUnitEntity, SensorEntity):
    """Estimated cost of this season's units, at the price set in the options.

    Only the consumption part of a heating bill depends on the units; fixed costs
    such as the share by floor area are not in it.
    """

    key = "season_cost"
    _attr_translation_key = "season_cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: MijntedCoordinator, unit_id: int, price: float
    ) -> None:
        """Initialize the sensor with the price per unit."""
        super().__init__(coordinator, unit_id, self.key)
        self._price = price
        self._attr_native_unit_of_measurement = coordinator.hass.config.currency

    @property
    def _season_total(self) -> float | None:
        totals = self.coordinator.data.totals.get(self._unit_id)
        return totals[max(totals)] if totals else None

    @property
    def native_value(self) -> float | None:
        """Return the season total times the price."""
        total = self._season_total
        return None if total is None else round(total * self._price, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the price and the season total it is based on."""
        return {"price_per_unit": self._price, "season_total": self._season_total}
