"""Locks on Zigbee Home Automation networks."""
import functools
import logging

import voluptuous as vol
from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.lock import STATE_LOCKED, STATE_UNLOCKED, LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
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
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the zhaws sensors from config entry."""
    unsub = async_dispatcher_connect(
        hass,
        SIGNAL_ADD_ENTITIES,
        functools.partial(add_entities, async_add_entities, Platform.LOCK, _LOGGER),
    )
    config_entry.async_on_unload(unsub)

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


@REGISTER_CLASS()
class Lock(ZhaEntity, LockEntity):
    """Representation of a ZHA lock."""

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        super().__init__(*args, **kwargs)
        self._state = (
            STATE_LOCKED if self._platform_entity.state.is_locked else STATE_UNLOCKED
        )

    @property
    def is_locked(self) -> bool:
        """Return true if entity is locked."""
        if self._state is None:
            return False
        return self._state == STATE_LOCKED

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.debug("Handling platform entity state changed: %s", event)
        self._state = STATE_LOCKED if event.state.is_locked else STATE_UNLOCKED
        self.async_write_ha_state()

    async def async_lock(self, **kwargs):
        """Lock the lock."""
        await self.device_or_group.controller.locks.lock(self._platform_entity)

    async def async_unlock(self, **kwargs):
        """Unlock the lock."""
        await self.device_or_group.controller.locks.unlock(self._platform_entity)

    async def async_set_lock_user_code(self, code_slot: int, user_code: str) -> None:
        """Set the user_code to index X on the lock."""
        await self.device_or_group.controller.locks.set_user_lock_code(
            self._platform_entity,
            code_slot,
            user_code,
        )
        self.debug("User code at slot %s set", code_slot)

    async def async_enable_lock_user_code(self, code_slot: int) -> None:
        """Enable user_code at index X on the lock."""
        await self.device_or_group.controller.locks.enable_user_lock_code(
            self._platform_entity, code_slot
        )
        self.debug("User code at slot %s enabled", code_slot)

    async def async_disable_lock_user_code(self, code_slot: int) -> None:
        """Disable user_code at index X on the lock."""
        await self.device_or_group.controller.locks.disable_user_lock_code(
            self._platform_entity, code_slot
        )
        self.debug("User code at slot %s disabled", code_slot)

    async def async_clear_lock_user_code(self, code_slot: int) -> None:
        """Clear the user_code at index X on the lock."""
        await self.device_or_group.controller.locks.clear_user_lock_code(
            self._platform_entity, code_slot
        )
        self.debug("User code at slot %s cleared", code_slot)
