"""Support for ZHA button."""
from __future__ import annotations

import functools
import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ENTITY_CATEGORY_DIAGNOSTIC, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY
from .const import ZHAWS
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.BUTTON)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the zhaws sensors from config entry."""
    entities: list[ZHAButton] = []
    devices = hass.data[ZHAWS][config_entry.entry_id].devices
    for device in devices.values():
        for entity in device.device.entities.values():
            _LOGGER.debug("processed entity: %s", entity)
            if entity.platform != Platform.BUTTON:
                continue
            entity_class = ENTITY_CLASS_REGISTRY[Platform.BUTTON][entity.class_name]
            _LOGGER.warning(
                "Creating entity: %s with class: %s", entity, entity_class.__name__
            )
            entities.append(entity_class(device, entity))

    async_add_entities(entities)


class ZHAButton(ZhaEntity, ButtonEntity):
    """Defines a ZHA button."""

    _command_name: str | None = None

    async def async_press(self) -> None:
        """Send out a update command."""
        await self._device.controller.buttons.press(self._platform_entity)


@REGISTER_CLASS()
class IdentifyButton(ZHAButton):
    """Defines a ZHA identify button."""

    _attr_device_class: ButtonDeviceClass = ButtonDeviceClass.UPDATE
    _attr_entity_category = ENTITY_CATEGORY_DIAGNOSTIC
    _command_name = "identify"
