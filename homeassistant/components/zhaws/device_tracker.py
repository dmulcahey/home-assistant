"""Support for the ZHA platform."""
from __future__ import annotations

import functools
import logging

from zhaws.client.model.events import PlatformEntityStateChangedEvent

from homeassistant.components.device_tracker import SOURCE_TYPE_ROUTER
from homeassistant.components.device_tracker.config_entry import ScannerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(
    ENTITY_CLASS_REGISTRY.register, Platform.DEVICE_TRACKER
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
            add_entities, async_add_entities, Platform.DEVICE_TRACKER, _LOGGER
        ),
    )
    config_entry.async_on_unload(unsub)


@REGISTER_CLASS()
class DeviceTracker(ScannerEntity, ZhaEntity):
    """Represent a tracked device."""

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA device tracker."""
        super().__init__(*args, **kwargs)
        self._connected = self._platform_entity.state.connected
        self._battery_level = None

    @property
    def is_connected(self):
        """Return true if the device is connected to the network."""
        return self._connected

    @property
    def source_type(self):
        """Return the source type, eg gps or router, of the device."""
        return SOURCE_TYPE_ROUTER

    @callback
    def platform_entity_state_changed(
        self, event: PlatformEntityStateChangedEvent
    ) -> None:
        """Set the entity state."""
        _LOGGER.debug("Handling platform entity state changed: %s", event)
        self._connected = event.state.connected
        self._battery_level = event.state.battery_level
        self.async_write_ha_state()

    @property
    def battery_level(self):
        """Return the battery level of the device.

        Percentage from 0-100.
        """
        return self._battery_level

    @property  # type: ignore
    def device_info(  # pylint: disable=overridden-final-method
        self,
    ) -> DeviceInfo | None:
        """Return device info."""
        # We opt ZHA device tracker back into overriding this method because
        # it doesn't track IP-based devices.
        # Call Super because ScannerEntity overrode it.
        return super(ZhaEntity, self).device_info

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        # Call Super because ScannerEntity overrode it.
        return super(ZhaEntity, self).unique_id
