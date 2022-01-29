"""Support for ZHA sirens."""
from __future__ import annotations

import functools
import logging
from typing import Any

from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.siren import (
    ATTR_DURATION,
    SUPPORT_DURATION,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SirenEntity,
)
from homeassistant.components.siren.const import (
    ATTR_TONE,
    ATTR_VOLUME_LEVEL,
    SUPPORT_TONES,
    SUPPORT_VOLUME_SET,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.SIREN)
WARNING_DEVICE_MODE_BURGLAR = 1
WARNING_DEVICE_MODE_FIRE = 2
WARNING_DEVICE_MODE_EMERGENCY = 3
WARNING_DEVICE_MODE_POLICE_PANIC = 4
WARNING_DEVICE_MODE_FIRE_PANIC = 5
WARNING_DEVICE_MODE_EMERGENCY_PANIC = 6

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
        functools.partial(add_entities, async_add_entities, Platform.SIREN, _LOGGER),
    )
    config_entry.async_on_unload(unsub)


@REGISTER_CLASS()
class Siren(ZhaEntity, SirenEntity):
    """Representation of a ZHA siren."""

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        self._attr_supported_features = (
            SUPPORT_TURN_ON
            | SUPPORT_TURN_OFF
            | SUPPORT_DURATION
            | SUPPORT_VOLUME_SET
            | SUPPORT_TONES
        )
        self._attr_available_tones: list[int | str] | dict[int, str] | None = {
            WARNING_DEVICE_MODE_BURGLAR: "Burglar",
            WARNING_DEVICE_MODE_FIRE: "Fire",
            WARNING_DEVICE_MODE_EMERGENCY: "Emergency",
            WARNING_DEVICE_MODE_POLICE_PANIC: "Police Panic",
            WARNING_DEVICE_MODE_FIRE_PANIC: "Fire Panic",
            WARNING_DEVICE_MODE_EMERGENCY_PANIC: "Emergency Panic",
        }
        super().__init__(*args, **kwargs)
        self._attr_is_on: bool = self._platform_entity.state.state

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._attr_is_on = bool(event.state.state)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on siren."""
        await self.device_or_group.controller.sirens.turn_on(
            self._platform_entity,
            tone=kwargs.get(ATTR_TONE),
            duration=kwargs.get(ATTR_DURATION),
            volume_level=kwargs.get(ATTR_VOLUME_LEVEL),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off siren."""
        await self.device_or_group.controller.sirens.turn_off(self._platform_entity)
