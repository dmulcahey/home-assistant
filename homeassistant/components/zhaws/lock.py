"""Locks on Zigbee Home Automation networks."""
import functools
import logging

import voluptuous as vol
from zhaws.client.model.commands import CommandResponse
from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.lock import STATE_LOCKED, STATE_UNLOCKED, LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform

from . import ENTITY_CLASS_REGISTRY
from .const import ZHAWS
from .entity import ZhaEntity

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.LOCK)

_LOGGER = logging.getLogger(__name__)

# The first state is Zigbee 'Not fully locked'
STATE_LIST = [STATE_UNLOCKED, STATE_LOCKED, STATE_UNLOCKED]

VALUE_TO_STATE = dict(enumerate(STATE_LIST))

SERVICE_SET_LOCK_USER_CODE = "set_lock_user_code"
SERVICE_ENABLE_LOCK_USER_CODE = "enable_lock_user_code"
SERVICE_DISABLE_LOCK_USER_CODE = "disable_lock_user_code"
SERVICE_CLEAR_LOCK_USER_CODE = "clear_lock_user_code"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: entity_platform.AddEntitiesCallback,
) -> None:
    """Set up the Flo sensors from config entry."""
    entities: list[Lock] = []
    devices = hass.data[ZHAWS][config_entry.entry_id].devices
    for device in devices.values():
        for entity in device.device.entities.values():
            _LOGGER.debug("processed entity: %s", entity)
            if entity.platform != Platform.LOCK:
                continue
            entity_class = ENTITY_CLASS_REGISTRY[Platform.LOCK][entity.class_name]
            _LOGGER.warning(
                "Creating entity: %s with class: %s", entity, entity_class.__name__
            )
            entities.append(entity_class(device, entity))

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_SET_LOCK_USER_CODE,
        {
            vol.Required("code_slot"): vol.Coerce(int),
            vol.Required("user_code"): cv.string,
        },
        "async_set_lock_user_code",
    )

    platform.async_register_entity_service(
        SERVICE_ENABLE_LOCK_USER_CODE,
        {
            vol.Required("code_slot"): vol.Coerce(int),
        },
        "async_enable_lock_user_code",
    )

    platform.async_register_entity_service(
        SERVICE_DISABLE_LOCK_USER_CODE,
        {
            vol.Required("code_slot"): vol.Coerce(int),
        },
        "async_disable_lock_user_code",
    )

    platform.async_register_entity_service(
        SERVICE_CLEAR_LOCK_USER_CODE,
        {
            vol.Required("code_slot"): vol.Coerce(int),
        },
        "async_clear_lock_user_code",
    )

    async_add_entities(entities)


@REGISTER_CLASS()
class Lock(ZhaEntity, LockEntity):
    """Representation of a ZHA lock."""

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        self._state = None
        super().__init__(*args, **kwargs)

    @property
    def is_locked(self) -> bool:
        """Return true if entity is locked."""
        if self._state is None:
            return False
        return self._state == STATE_LOCKED

    @callback
    def async_restore_last_state(self, last_state):
        """Restore previous state."""
        self._state = VALUE_TO_STATE.get(last_state.state, last_state.state)

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = STATE_LOCKED if event.state["is_locked"] else STATE_UNLOCKED
        self.async_write_ha_state()

    async def async_lock(self, **kwargs):
        """Lock the lock."""
        result: CommandResponse = await self._device.controller.locks.lock(
            self._platform_entity
        )
        if not result.success:
            return
        self._state = STATE_LOCKED
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs):
        """Unlock the lock."""
        result: CommandResponse = await self._device.controller.locks.unlock(
            self._platform_entity
        )
        if not result.success:
            return
        self._state = STATE_UNLOCKED
        self.async_write_ha_state()

    async def async_set_lock_user_code(self, code_slot: int, user_code: str) -> None:
        """Set the user_code to index X on the lock."""
        await self._device.controller.locks.set_user_lock_code(
            self._platform_entity,
            code_slot,
            user_code,
        )
        self.debug("User code at slot %s set", code_slot)

    async def async_enable_lock_user_code(self, code_slot: int) -> None:
        """Enable user_code at index X on the lock."""
        await self._device.controller.locks.enable_user_lock_code(
            self._platform_entity, code_slot
        )
        self.debug("User code at slot %s enabled", code_slot)

    async def async_disable_lock_user_code(self, code_slot: int) -> None:
        """Disable user_code at index X on the lock."""
        await self._device.controller.locks.disable_user_lock_code(
            self._platform_entity, code_slot
        )
        self.debug("User code at slot %s disabled", code_slot)

    async def async_clear_lock_user_code(self, code_slot: int) -> None:
        """Clear the user_code at index X on the lock."""
        await self._device.controller.locks.clear_user_lock_code(
            self._platform_entity, code_slot
        )
        self.debug("User code at slot %s cleared", code_slot)
