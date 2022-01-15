"""Binary sensors on Zigbee Home Automation networks."""
import logging

from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ZHAWS
from .entity import ZhaEntity

# Zigbee Cluster Library Zone Type to Home Assistant device class
CLASS_MAPPING = {
    0x000D: BinarySensorDeviceClass.MOTION,
    0x0015: BinarySensorDeviceClass.OPENING,
    0x0028: BinarySensorDeviceClass.SMOKE,
    0x002A: BinarySensorDeviceClass.MOISTURE,
    0x002B: BinarySensorDeviceClass.GAS,
    0x002D: BinarySensorDeviceClass.VIBRATION,
}

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Flo sensors from config entry."""
    entities: list[BinarySensor] = []
    devices = hass.data[ZHAWS][config_entry.entry_id].devices
    for device in devices.values():
        for entity in device.device.entities.values():
            _LOGGER.info("processed entity: %s", entity)
            if entity.platform != Platform.BINARY_SENSOR.name:
                continue
            if entity.class_name == "Accelerometer":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Accelerometer(device, entity))
            elif entity.class_name == "BinaryInput":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(BinaryInput(device, entity))
            elif entity.class_name == "Occupancy":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Occupancy(device, entity))
            elif entity.class_name == "Opening":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Opening(device, entity))
            elif entity.class_name == "Motion":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Motion(device, entity))
            elif entity.class_name == "IASZone":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(IASZone(device, entity))

    async_add_entities(entities)


class BinarySensor(ZhaEntity, BinarySensorEntity):
    """ZHA BinarySensor."""

    @callback
    def async_restore_last_state(self, last_state):
        """Restore previous state."""
        super().async_restore_last_state(last_state)
        self._state = last_state.state == STATE_ON

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = bool(event.state)
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on based on the state machine."""
        if self._state is None:
            return False
        return self._state


class Accelerometer(BinarySensor):
    """ZHA BinarySensor."""

    _attr_device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.MOVING


class Occupancy(BinarySensor):
    """ZHA BinarySensor."""

    _attr_device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.OCCUPANCY


class Opening(BinarySensor):
    """ZHA BinarySensor."""

    _attr_device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.OPENING


class BinaryInput(BinarySensor):
    """ZHA BinarySensor."""


class Motion(BinarySensor):
    """ZHA BinarySensor."""

    _attr_device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.MOTION


class IASZone(BinarySensor):
    """ZHA IAS BinarySensor."""

    """TODO
    @property
    def device_class(self) -> str:
        #Return device class from component DEVICE_CLASSES.
        return CLASS_MAPPING.get(self._channel.cluster.get("zone_type"))
    """
