"""Alarm control panels on Zigbee Home Automation networks."""
from __future__ import annotations

import functools
import logging

from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.alarm_control_panel import (
    FORMAT_TEXT,
    SUPPORT_ALARM_ARM_AWAY,
    SUPPORT_ALARM_ARM_HOME,
    SUPPORT_ALARM_ARM_NIGHT,
    SUPPORT_ALARM_TRIGGER,
    AlarmControlPanelEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY
from .const import ZHAWS
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(
    ENTITY_CLASS_REGISTRY.register, Platform.ALARM_CONTROL_PANEL
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the zhaws sensors from config entry."""
    entities: list[ZHAAlarmControlPanel] = []
    devices = hass.data[ZHAWS][config_entry.entry_id].devices
    for device in devices.values():
        for entity in device.device.entities.values():
            _LOGGER.debug("processed entity: %s", entity)
            if entity.platform != Platform.ALARM_CONTROL_PANEL:
                continue
            entity_class = ENTITY_CLASS_REGISTRY[Platform.ALARM_CONTROL_PANEL][
                entity.class_name
            ]
            _LOGGER.warning(
                "Creating entity: %s with class: %s", entity, entity_class.__name__
            )
            entities.append(entity_class(device, entity))

    async_add_entities(entities)


@REGISTER_CLASS()
class ZHAAlarmControlPanel(ZhaEntity, AlarmControlPanelEntity):
    """Entity for ZHA alarm control devices."""

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        self._state = None
        super().__init__(*args, **kwargs)

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = event.state
        self.async_write_ha_state()

    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""
        return FORMAT_TEXT

    @property
    def changed_by(self):
        """Last change triggered by."""
        return None

    @property
    def code_arm_required(self):
        """Whether the code is required for arm actions."""
        return True  # TODO fix this

    async def async_alarm_disarm(self, code=None):
        """Send disarm command."""
        await self._device.controller.alarm_control_panels.disarm(
            self._platform_entity, code
        )

    async def async_alarm_arm_home(self, code=None):
        """Send arm home command."""
        await self._device.controller.alarm_control_panels.arm_home(
            self._platform_entity, code
        )

    async def async_alarm_arm_away(self, code=None):
        """Send arm away command."""
        await self._device.controller.alarm_control_panels.arm_away(
            self._platform_entity, code
        )

    async def async_alarm_arm_night(self, code=None):
        """Send arm night command."""
        await self._device.controller.alarm_control_panels.arm_night(
            self._platform_entity, code
        )

    async def async_alarm_trigger(self, code=None):
        """Send alarm trigger command."""
        await self._device.controller.alarm_control_panels.trigger(
            self._platform_entity, code
        )

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return (
            SUPPORT_ALARM_ARM_HOME
            | SUPPORT_ALARM_ARM_AWAY
            | SUPPORT_ALARM_ARM_NIGHT
            | SUPPORT_ALARM_TRIGGER
        )

    @property
    def state(self):
        """Return the state of the entity."""
        return self._state
