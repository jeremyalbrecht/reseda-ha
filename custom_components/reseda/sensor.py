"""Sensors exposing per-contract metadata and the latest meter reading."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TZ_PARIS
from .coordinator import ResedaConfigEntry, ResedaCoordinator, ResedaData


@dataclass(frozen=True, kw_only=True)
class ResedaSensorDescription(SensorEntityDescription):
    """Pairs an EntityDescription with a value getter."""

    value_fn: Callable[[ResedaData], str | float | datetime | None]


def _last_reading_dt(data: ResedaData) -> datetime | None:
    if data.last_reading_date is None:
        return None
    return datetime.combine(data.last_reading_date, time.min, tzinfo=TZ_PARIS)


SENSORS: tuple[ResedaSensorDescription, ...] = (
    ResedaSensorDescription(
        key="consumption_total",
        translation_key="consumption_total",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.consumption_total_kwh,
    ),
    ResedaSensorDescription(
        key="last_reading_date",
        translation_key="last_reading_date",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_reading_dt,
    ),
    ResedaSensorDescription(
        key="prm",
        translation_key="prm",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.pasc.pds_reference or None,
    ),
    ResedaSensorDescription(
        key="address",
        translation_key="address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            f"{d.address.line}, {d.address.code_postal} {d.address.commune}"
            if d.address
            else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ResedaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Réséda sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        ResedaSensor(coordinator, description) for description in SENSORS
    )


class ResedaSensor(CoordinatorEntity[ResedaCoordinator], SensorEntity):
    """A single Réséda sensor."""

    _attr_has_entity_name = True
    entity_description: ResedaSensorDescription

    def __init__(
        self,
        coordinator: ResedaCoordinator,
        description: ResedaSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        pasc = coordinator.data.pasc
        self._attr_unique_id = f"{pasc.id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pasc.id)},
            manufacturer="Réséda",
            model=pasc.activity or "Électricité",
            name=f"Réséda {pasc.pds_reference or pasc.reference}",
            configuration_url="https://monagence.reseda.fr/",
        )

    @property
    def native_value(self) -> str | float | datetime | date | None:
        """Return the value for this sensor from coordinator data."""
        return self.entity_description.value_fn(self.coordinator.data)
