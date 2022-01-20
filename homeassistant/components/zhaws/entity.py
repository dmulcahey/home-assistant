"""Entity for Zigbee Home Automation."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from zhaws.client.device import Device
from zhaws.client.model.events import PlatformEntityEvent
from zhaws.client.model.types import BasePlatformEntity

from homeassistant.core import callback
from homeassistant.helpers import entity
from homeassistant.helpers.device_registry import CONNECTION_ZIGBEE

from .const import COORDINATOR_IEEE, DOMAIN, ZHAWS

_LOGGER = logging.getLogger(__name__)

ENTITY_SUFFIX = "entity_suffix"
UPDATE_GROUP_FROM_CHILD_DELAY = 0.5


class LogMixin:
    """Log helper."""

    def log(self, level, msg, *args):
        """Log with level."""
        raise NotImplementedError

    def debug(self, msg, *args):
        """Debug level log."""
        return self.log(logging.DEBUG, msg, *args)

    def info(self, msg, *args):
        """Info level log."""
        return self.log(logging.INFO, msg, *args)

    def warning(self, msg, *args):
        """Warning method log."""
        return self.log(logging.WARNING, msg, *args)

    def error(self, msg, *args):
        """Error level log."""
        return self.log(logging.ERROR, msg, *args)


class BaseZhaEntity(LogMixin, entity.Entity):
    """A base class for ZHA entities."""

    def __init__(
        self,
        device: Device,
        platform_entity: BasePlatformEntity,
        **kwargs,
    ) -> None:
        """Init ZHA entity."""
        self._device: Device = device
        self._platform_entity: BasePlatformEntity = platform_entity
        self._name: str = platform_entity.name
        self._force_update: bool = False
        self._should_poll: bool = False
        self._unique_id: str = platform_entity.unique_id
        self._state: Any = None
        self._extra_state_attributes: dict[str, Any] = {}
        self.remove_future: asyncio.Future[bool] = asyncio.Future()

    @property
    def name(self) -> str:
        """Return Entity's default name."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    @property
    def device(self) -> Device:
        """Return the device this entity is attached to."""
        return self._device

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device specific state attributes."""
        return self._extra_state_attributes

    @property
    def force_update(self) -> bool:
        """Force update this entity."""
        return self._force_update

    @property
    def should_poll(self) -> bool:
        """Poll state from device."""
        return self._should_poll

    @property
    def device_info(self) -> entity.DeviceInfo | None:
        """Return a device description for device registry."""
        if hasattr(self._device, "ieee"):
            return entity.DeviceInfo(
                connections={(CONNECTION_ZIGBEE, self.device.device.ieee)},
                identifiers={(DOMAIN, self.device.device.ieee)},
                manufacturer=self.device.device.manufacturer,
                model=self.device.device.model,
                name=self.device.device.name,
                via_device=(DOMAIN, self.hass.data[ZHAWS][COORDINATOR_IEEE]),
            )
        return None

    @callback
    def async_state_changed(self) -> None:
        """Entity state changed."""
        self.async_write_ha_state()

    @callback
    def async_update_state_attribute(self, key: str, value: Any) -> None:
        """Update a single device state attribute."""
        self._extra_state_attributes.update({key: value})
        self.async_write_ha_state()

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""

    def log(self, level: int, msg: str, *args):
        """Log a message."""
        msg = f"%s: {msg}"
        args = (self.entity_id,) + args
        _LOGGER.log(level, msg, *args)


class ZhaEntity(BaseZhaEntity):
    """A base class for non group ZHA entities."""

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return True
        # TODO force all available for now
        # return self._device.device.available

    async def async_added_to_hass(self) -> None:
        """Run when about to be added to hass."""
        self.remove_future = asyncio.Future()
        self._platform_entity.on_event(
            "platform_entity_state_changed", self.platform_entity_state_changed
        )

    async def async_will_remove_from_hass(self) -> None:
        """Disconnect entity object when removed."""
        await super().async_will_remove_from_hass()
        self.remove_future.set_result(True)

    async def async_update(self) -> None:
        """Retrieve latest state."""
        await self._device.controller.entities.refresh_state(self._platform_entity)


"""
class ZhaGroupEntity(BaseZhaEntity):
    #A base class for ZHA group entities.

    def __init__(
        self, entity_ids: list[str], unique_id: str, group_id: int, zha_device, **kwargs
    ) -> None:
        #Initialize a light group.
        super().__init__(unique_id, zha_device, **kwargs)
        self._available = False
        self._group = zha_device.gateway.groups.get(group_id)
        self._name = f"{self._group.name}_zha_group_0x{group_id:04x}"
        self._group_id: int = group_id
        self._entity_ids: list[str] = entity_ids
        self._async_unsub_state_changed: CALLBACK_TYPE | None = None
        self._handled_group_membership = False
        self._change_listener_debouncer: Debouncer | None = None

    @property
    def available(self) -> bool:
        #Return entity availability.
        return self._available

    @classmethod
    def create_entity(
        cls, entity_ids: list[str], unique_id: str, group_id: int, zha_device, **kwargs
    ) -> ZhaGroupEntity | None:
        #Group Entity Factory.

        #Return entity if it is a supported configuration, otherwise return None

        return cls(entity_ids, unique_id, group_id, zha_device, **kwargs)

    async def _handle_group_membership_changed(self):
        #Handle group membership changed.
        # Make sure we don't call remove twice as members are removed
        if self._handled_group_membership:
            return

        self._handled_group_membership = True
        await self.async_remove(force_remove=True)

    async def async_added_to_hass(self) -> None:
        #Register callbacks.
        await super().async_added_to_hass()

        self.async_accept_signal(
            None,
            f"{SIGNAL_GROUP_MEMBERSHIP_CHANGE}_0x{self._group_id:04x}",
            self._handle_group_membership_changed,
            signal_override=True,
        )

        if self._change_listener_debouncer is None:
            self._change_listener_debouncer = Debouncer(
                self.hass,
                self,
                cooldown=UPDATE_GROUP_FROM_CHILD_DELAY,
                immediate=False,
                function=functools.partial(self.async_update_ha_state, True),
            )
        self._async_unsub_state_changed = async_track_state_change_event(
            self.hass, self._entity_ids, self.async_state_changed_listener
        )

        def send_removed_signal():
            async_dispatcher_send(
                self.hass, SIGNAL_GROUP_ENTITY_REMOVED, self._group_id
            )

        self.async_on_remove(send_removed_signal)

    @callback
    def async_state_changed_listener(self, event: Event):
        #Handle child updates.
        # Delay to ensure that we get updates from all members before updating the group
        self.hass.create_task(self._change_listener_debouncer.async_call())

    async def async_will_remove_from_hass(self) -> None:
        #Handle removal from Home Assistant.
        await super().async_will_remove_from_hass()
        if self._async_unsub_state_changed is not None:
            self._async_unsub_state_changed()
            self._async_unsub_state_changed = None

    async def async_update(self) -> None:
        #Update the state of the group entity.
"""
