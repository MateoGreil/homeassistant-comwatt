"""Support for Comwatt sensors."""
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ENERGY_KILO_WATT_HOUR, POWER_WATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME, SENSORS

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Comwatt sensor platform."""
    data: dict = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: DataUpdateCoordinator = data[config_entry.entry_id]
    comwatt_data: dict = coordinator.data
    comwatt_name: str = data[NAME]
    comwatt_id = config_entry.unique_id
    assert comwatt_id is not None
    _LOGGER.debug("Comwatt data: %s", comwatt_data)

    entities: list[ComwattSensor] = []
    for description in SENSORS:
        if description.key in comwatt_data:
            entities.append(
                ComwattSensor(
                    coordinator,
                    description,
                    comwatt_name,
                    comwatt_id,
                )
            )

    async_add_entities(entities)


class ComwattSensor(CoordinatorEntity, SensorEntity):
    """Comwatt sensor entity."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: SensorEntityDescription,
        comwatt_name: str,
        comwatt_id: str,
    ) -> None:
        """Initialize Comwatt sensor entity."""
        self.entity_description = description
        self._attr_name = f"{comwatt_name} {description.name}"
        self._attr_unique_id = f"comwatt_{comwatt_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, comwatt_id)},
            manufacturer="Comwatt",
            name=comwatt_name,
        )
        super().__init__(coordinator)

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if (value := self.coordinator.data.get(self.entity_description.key)) is None:
            return None
        return float(value)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        return self.entity_description.unit


SENSOR_TYPES: tuple[SensorEntityDescription] = (
    SensorEntityDescription(
        key="power",
        name="Power",
        unit=POWER_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    SensorEntityDescription(
        key="energy_today",
        name="Energy Today",
        unit=ENERGY_KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ENERGY,
    ),
    # Add more sensor descriptions as needed
)
