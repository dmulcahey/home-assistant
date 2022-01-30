"""Entity for Zigbee Home Automation."""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

from zhaws.client.model.events import PlatformEntityStateChangedEvent
from zhaws.client.model.types import BasePlatformEntity
from zhaws.client.proxy import DeviceProxy, GroupProxy

from homeassistant.core import callback
from homeassistant.helpers import entity
from homeassistant.helpers.device_registry import CONNECTION_ZIGBEE
from homeassistant.helpers.dispatcher import async_dispatcher_connect

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
        parent_proxy: DeviceProxy | GroupProxy,
        platform_entity: BasePlatformEntity,
        **kwargs,
    ) -> None:
        """Init ZHA entity."""
        self._parent_proxy: DeviceProxy | GroupProxy = parent_proxy
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
    def device_or_group(self) -> DeviceProxy | GroupProxy:
        """Return the device or group this entity is attached to."""
        return self._parent_proxy

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
        if hasattr(self._parent_proxy, "device_model"):
            return entity.DeviceInfo(
                connections={
                    (CONNECTION_ZIGBEE, self.device_or_group.device_model.ieee)
                },
                identifiers={(DOMAIN, self.device_or_group.device_model.ieee)},
                manufacturer=self.device_or_group.device_model.manufacturer,
                model=self.device_or_group.device_model.model,
                name=self.device_or_group.device_model.name,
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
    def platform_entity_state_changed(
        self, event: PlatformEntityStateChangedEvent
    ) -> None:
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
        # TODO force all available for now # pylint: disable=w0511
        # return self.device_or_group.device.available

    async def async_added_to_hass(self) -> None:
        """Run when about to be added to hass."""
        self.remove_future = asyncio.Future()
        if isinstance(self.device_or_group, DeviceProxy):
            async_dispatcher_connect(
                self.hass,
                f"remove_device_{self._parent_proxy.device_model.ieee}",
                functools.partial(self.async_remove, force_remove=True),
            )
        self.device_or_group.on_event(
            f"{self._platform_entity.unique_id}_platform_entity_state_changed",
            self.platform_entity_state_changed,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Disconnect entity object when removed."""
        await super().async_will_remove_from_hass()
        self.remove_future.set_result(True)

    async def async_update(self) -> None:
        """Retrieve latest state."""
        await self.device_or_group.controller.entities.refresh_state(
            self._platform_entity
        )
