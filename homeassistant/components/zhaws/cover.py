"""Support for ZHA covers."""
from __future__ import annotations

import functools
import logging

from zhaws.client.model.commands import CommandResponse
from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.COVER)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the zhaws covers from config entry."""
    unsub = async_dispatcher_connect(
        hass,
        SIGNAL_ADD_ENTITIES,
        functools.partial(add_entities, async_add_entities, Platform.COVER, _LOGGER),
    )
    config_entry.async_on_unload(unsub)


class BaseCover(ZhaEntity, CoverEntity):
    """Representation of a ZHA cover."""

    def __init__(self, *args, **kwargs):
        """Init this cover."""
        super().__init__(*args, **kwargs)
        self._current_position = None
        self._state = None

    @callback
    def async_restore_last_state(self, last_state):
        """Restore previous state."""
        self._state = last_state.state
        if ATTR_CURRENT_POSITION in last_state.attributes:
            self._current_position = last_state.attributes[ATTR_CURRENT_POSITION]

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        if self.current_cover_position is None:
            return None
        return self.current_cover_position == 0

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        return self._state == STATE_OPENING

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        return self._state == STATE_CLOSING

    @property
    def current_cover_position(self):
        """Return the current position of ZHA cover.

        None is unknown, 0 is closed, 100 is fully open.
        """
        return self._current_position

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        _LOGGER.debug("setting position: %s", event.state)
        self._current_position = event.state["current_position"]
        self._state = STATE_CLOSED if event.state["is_closed"] else STATE_OPEN
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs):
        """Open the window cover."""
        result: CommandResponse = await self._device.controller.covers.open_cover(
            self._platform_entity,
        )
        if not result.success:
            return
        self._state = STATE_OPENING
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs):
        """Close the window cover."""
        result: CommandResponse = await self._device.controller.covers.close_cover(
            self._platform_entity,
        )
        if not result.success:
            return
        self._state = STATE_CLOSING
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Move the roller shutter to a specific position."""
        new_pos = kwargs[ATTR_POSITION]
        result: CommandResponse = (
            await self._device.controller.covers.set_cover_position(
                self._platform_entity, new_pos
            )
        )
        if not result.success:
            return
        self._state = (
            STATE_CLOSING if new_pos < self._current_position else STATE_OPENING
        )
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs):
        """Stop the window cover."""
        result: CommandResponse = await self._device.controller.covers.stop_cover(
            self._platform_entity,
        )
        if not result.success:
            return
        self._state = STATE_OPEN if self._current_position > 0 else STATE_CLOSED
        self.async_write_ha_state()


@REGISTER_CLASS()
class ZhaCover(ZhaEntity, CoverEntity):
    """Representation of a ZHA cover."""

    def __init__(self, *args, **kwargs):
        """Init this cover."""
        super().__init__(*args, **kwargs)
        self._current_position = self._platform_entity.state.current_position
        self._state = self._platform_entity.state.state


@REGISTER_CLASS()
class Shade(BaseCover):
    """ZHA Shade."""

    _attr_device_class = CoverDeviceClass.SHADE


@REGISTER_CLASS()
class KeenVent(Shade):
    """Keen vent cover."""

    _attr_device_class = CoverDeviceClass.DAMPER
