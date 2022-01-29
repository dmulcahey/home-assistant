"""Support for ZHA button."""
from __future__ import annotations

import functools
import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.BUTTON)

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
        functools.partial(add_entities, async_add_entities, Platform.BUTTON, _LOGGER),
    )
    config_entry.async_on_unload(unsub)


class ZHAButton(ZhaEntity, ButtonEntity):
    """Defines a ZHA button."""

    _command_name: str | None = None

    async def async_press(self) -> None:
        """Send out a update command."""
        await self.device_or_group.controller.buttons.press(self._platform_entity)


@REGISTER_CLASS()
class IdentifyButton(ZHAButton):
    """Defines a ZHA identify button."""

    _attr_device_class: ButtonDeviceClass = ButtonDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _command_name = "identify"
