"""Support for ZHA controls using the select platform."""
from __future__ import annotations

import functools
import logging

from zhaws.client.device import Device
from zhaws.client.model.commands import CommandResponse
from zhaws.client.model.types import BasePlatformEntity

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ENTITY_CATEGORY_CONFIG, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY
from .const import ZHAWS
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.SELECT)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Flo sensors from config entry."""
    entities: list[EnumSelectEntity] = []
    devices = hass.data[ZHAWS][config_entry.entry_id].devices
    for device in devices.values():
        for entity in device.device.entities.values():
            _LOGGER.debug("processed entity: %s", entity)
            if entity.platform != Platform.SELECT:
                continue
            entity_class = ENTITY_CLASS_REGISTRY[Platform.SELECT][entity.class_name]
            _LOGGER.warning(
                "Creating entity: %s with class: %s", entity, entity_class.__name__
            )
            entities.append(entity_class(device, entity))

    async_add_entities(entities)


@REGISTER_CLASS(
    alternate_class_names=[
        "DefaultStrobeSelectEntity",
        "DefaultStrobeLevelSelectEntity",
        "DefaultSirenLevelSelectEntity",
        "DefaultToneSelectEntity",
    ]
)
class EnumSelectEntity(ZhaEntity, SelectEntity):
    """Representation of a ZHA select entity."""

    _attr_entity_category = ENTITY_CATEGORY_CONFIG

    def __init__(self, device: Device, platform_entity: BasePlatformEntity, **kwargs):
        """Initialize the select."""
        self._attr_name = platform_entity.enum
        self._attr_options = platform_entity.options
        super().__init__(device, platform_entity, **kwargs)
        self._current_option: str | int | None = None

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return True

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option to represent the entity state."""
        return str(self._current_option)

    async def async_select_option(self, option: str | int) -> None:
        """Change the selected option."""
        result: CommandResponse = await self._device.controller.selects.select_option(
            self._platform_entity,
            option,
        )
        if not result.success:
            return
        self._current_option = option
        self.async_write_ha_state()

    @callback
    def async_restore_last_state(self, last_state) -> None:
        """Restore previous state."""
        if last_state.state and last_state.state != STATE_UNKNOWN:
            self._current_option = last_state.state
