"""Lights on Zigbee Home Automation networks."""
from __future__ import annotations

import functools
import logging
from typing import Any

from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components import light
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.LIGHT)

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
        functools.partial(add_entities, async_add_entities, Platform.LIGHT, _LOGGER),
    )
    config_entry.async_on_unload(unsub)


@REGISTER_CLASS(alternate_class_names=["HueLight", "ForceOnLight", "LightGroup"])
class Light(ZhaEntity, light.LightEntity):
    """Operations common to all light entities."""

    def __init__(self, *args, **kwargs):
        """Initialize the light."""
        super().__init__(*args, **kwargs)
        self._brightness: int | None = self._platform_entity.state.brightness
        self._off_brightness: int | None = self._platform_entity.state.off_brightness
        self._hs_color: tuple[
            float, float
        ] | None = self._platform_entity.state.hs_color
        self._color_temp: int | None = self._platform_entity.state.color_temp
        self._effect: str | None = self._platform_entity.state.effect
        self._state: bool | None = self._platform_entity.state.on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes."""
        attributes = {"off_brightness": self._off_brightness}
        return attributes

    @property
    def is_on(self) -> bool:
        """Return true if entity is on."""
        if self._state is None:
            return False
        return self._state

    @property
    def brightness(self):
        """Return the brightness of this light."""
        return self._brightness

    @property
    def min_mireds(self):
        """Return the coldest color_temp that this light supports."""
        return self._platform_entity.min_mireds

    @property
    def max_mireds(self):
        """Return the warmest color_temp that this light supports."""
        return self._platform_entity.max_mireds

    @property
    def hs_color(self):
        """Return the hs color value [int, int]."""
        return self._hs_color

    @property
    def color_temp(self):
        """Return the CT color value in mireds."""
        return self._color_temp

    @property
    def effect_list(self):
        """Return the list of supported effects."""
        return self._platform_entity.effect_list

    @property
    def effect(self):
        """Return the current effect."""
        return self._effect

    @property
    def supported_features(self):
        """Flag supported features."""
        return self._platform_entity.supported_features

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.debug("Handling platform entity state changed: %s", event)
        self._state = event.state.on
        self._brightness = event.state.brightness
        self._hs_color = event.state.hs_color
        self._color_temp = event.state.color_temp
        self._effect = event.state.effect
        self._off_brightness = event.state.off_brightness
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        await self.device_or_group.controller.lights.turn_on(
            self._platform_entity, **kwargs
        )

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        await self.device_or_group.controller.lights.turn_off(
            self._platform_entity, **kwargs
        )
