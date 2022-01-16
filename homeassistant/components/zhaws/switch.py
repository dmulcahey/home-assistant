"""Switches on Zigbee Home Automation networks."""
from __future__ import annotations

import functools
import logging

from zhaws.client.model.commands import CommandResponse
from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY
from .const import ZHAWS
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.SWITCH)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Flo sensors from config entry."""
    entities: list[Switch] = []
    devices = hass.data[ZHAWS][config_entry.entry_id].devices
    for device in devices.values():
        for entity in device.device.entities.values():
            _LOGGER.debug("processed entity: %s", entity)
            if entity.platform != Platform.SWITCH:
                continue
            entity_class = ENTITY_CLASS_REGISTRY[Platform.SWITCH][entity.class_name]
            _LOGGER.warning(
                "Creating entity: %s with class: %s", entity, entity_class.__name__
            )
            entities.append(entity_class(device, entity))

    async_add_entities(entities)


class BaseSwitch(SwitchEntity):
    """Common base class for zha switches."""

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        self._state = None
        super().__init__(*args, **kwargs)

    @property
    def is_on(self) -> bool:
        """Return if the switch is on based on the statemachine."""
        if self._state is None:
            return False
        return self._state


@REGISTER_CLASS()
class Switch(BaseSwitch, ZhaEntity):
    """ZHA switch."""

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = bool(event.state["state"])
        self.async_write_ha_state()

    @callback
    def async_restore_last_state(self, last_state) -> None:
        """Restore previous state."""
        self._state = last_state.state == STATE_ON

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        result: CommandResponse = await self._device.controller.switches.turn_on(
            self._platform_entity
        )
        if not result.success:
            return
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        result: CommandResponse = await self._device.controller.switches.turn_off(
            self._platform_entity
        )
        if not result.success:
            return
        self._state = False
        self.async_write_ha_state()


"""
@GROUP_MATCH()
class SwitchGroup(BaseSwitch, ZhaGroupEntity):
    #Representation of a switch group.

    def __init__(
        self, entity_ids: list[str], unique_id: str, group_id: int, zha_device, **kwargs
    ) -> None:
        #Initialize a switch group.
        super().__init__(entity_ids, unique_id, group_id, zha_device, **kwargs)
        self._available: bool = False
        group = self.zha_device.gateway.get_group(self._group_id)
        self._on_off_channel = group.endpoint[OnOff.cluster_id]

    async def async_update(self) -> None:
        #Query all members and determine the light group state.
        all_states = [self.hass.states.get(x) for x in self._entity_ids]
        states: list[State] = list(filter(None, all_states))
        on_states = [state for state in states if state.state == STATE_ON]

        self._state = len(on_states) > 0
        self._available = any(state.state != STATE_UNAVAILABLE for state in states)
"""
