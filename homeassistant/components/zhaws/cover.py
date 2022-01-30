"""Support for ZHA covers."""
from __future__ import annotations

import functools
import logging

from zhaws.client.model.events import PlatformEntityStateChangedEvent

from homeassistant.components.cover import ATTR_POSITION, CoverDeviceClass, CoverEntity
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
    def platform_entity_state_changed(
        self, event: PlatformEntityStateChangedEvent
    ) -> None:
        """Set the entity state."""
        _LOGGER.debug("Handling platform entity state changed: %s", event)
        _LOGGER.debug("setting position: %s", event.state)
        self._current_position = event.state.current_position
        self._state = STATE_CLOSED if event.state.is_closed else STATE_OPEN
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs):
        """Open the window cover."""
        await self.device_or_group.controller.covers.open_cover(
            self._platform_entity,
        )

    async def async_close_cover(self, **kwargs):
        """Close the window cover."""
        await self.device_or_group.controller.covers.close_cover(
            self._platform_entity,
        )

    async def async_set_cover_position(self, **kwargs):
        """Move the roller shutter to a specific position."""
        new_pos = kwargs[ATTR_POSITION]
        await self.device_or_group.controller.covers.set_cover_position(
            self._platform_entity, new_pos
        )

    async def async_stop_cover(self, **kwargs):
        """Stop the window cover."""
        await self.device_or_group.controller.covers.stop_cover(
            self._platform_entity,
        )


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

    def __init__(self, *args, **kwargs):
        """Init this cover."""
        super().__init__(*args, **kwargs)
        self._current_position = self._platform_entity.state.current_position
        self._state = (
            STATE_CLOSED if self._platform_entity.state.is_closed else STATE_OPEN
        )


@REGISTER_CLASS()
class KeenVent(Shade):
    """Keen vent cover."""

    _attr_device_class = CoverDeviceClass.DAMPER
