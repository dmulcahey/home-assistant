"""Binary sensors on Zigbee Home Automation networks."""
import functools
import logging

from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
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

REGISTER_CLASS = functools.partial(
    ENTITY_CLASS_REGISTRY.register, Platform.BINARY_SENSOR
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the zhaws sensors from config entry."""
    unsub = async_dispatcher_connect(
        hass,
        SIGNAL_ADD_ENTITIES,
        functools.partial(
            add_entities, async_add_entities, Platform.BINARY_SENSOR, _LOGGER
        ),
    )
    config_entry.async_on_unload(unsub)


class BinarySensor(ZhaEntity, BinarySensorEntity):
    """ZHA BinarySensor."""

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        super().__init__(*args, **kwargs)
        self._state = self._platform_entity.state.state

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning(
            "Handling platform entity: %s state changed: %s",
            f"{self.unique_id}-{self.entity_id}",
            event,
        )
        self._state = bool(event.state.state)
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on based on the state machine."""
        if self._state is None:
            return False
        return self._state


@REGISTER_CLASS()
class Accelerometer(BinarySensor):
    """ZHA BinarySensor."""

    _attr_device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.MOVING


@REGISTER_CLASS()
class Occupancy(BinarySensor):
    """ZHA BinarySensor."""

    _attr_device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.OCCUPANCY


@REGISTER_CLASS()
class Opening(BinarySensor):
    """ZHA BinarySensor."""

    _attr_device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.OPENING


@REGISTER_CLASS()
class BinaryInput(BinarySensor):
    """ZHA BinarySensor."""


@REGISTER_CLASS()
class Motion(BinarySensor):
    """ZHA BinarySensor."""

    _attr_device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.MOTION


@REGISTER_CLASS()
class IASZone(BinarySensor):
    """ZHA IAS BinarySensor."""

    """TODO
    @property
    def device_class(self) -> str:
        #Return device class from component DEVICE_CLASSES.
        return CLASS_MAPPING.get(self._channel.cluster.get("zone_type"))
    """
