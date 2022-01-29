"""Switches on Zigbee Home Automation networks."""
from __future__ import annotations

import functools
import logging

from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.SWITCH)

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
        functools.partial(add_entities, async_add_entities, Platform.SWITCH, _LOGGER),
    )
    config_entry.async_on_unload(unsub)


@REGISTER_CLASS(alternate_class_names=["SwitchGroup"])
class Switch(SwitchEntity, ZhaEntity):
    """ZHA switch."""

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        super().__init__(*args, **kwargs)
        self._state = self._platform_entity.state.state

    @property
    def is_on(self) -> bool:
        """Return if the switch is on based on the statemachine."""
        if self._state is None:
            return False
        return self._state

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.debug(
            "Handling platform entity state changed in switch group: %s", event
        )
        self._state = bool(event.state.state)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        await self.device_or_group.controller.switches.turn_on(self._platform_entity)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        await self.device_or_group.controller.switches.turn_off(self._platform_entity)
