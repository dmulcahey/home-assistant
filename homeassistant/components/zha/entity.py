"""Entity for Zigbee Home Automation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import functools
import logging
from typing import Any

from zha.application.platforms import EntityCategory as ZHAEntityCategory
from zha.mixins import LogMixin

from homeassistant.const import ATTR_MANUFACTURER, ATTR_MODEL, ATTR_NAME, EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import entity
from homeassistant.helpers.device_registry import CONNECTION_ZIGBEE, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN
from .helpers import SIGNAL_REMOVE_ENTITIES, EntityData

_LOGGER = logging.getLogger(__name__)

ENTITY_SUFFIX = "entity_suffix"
DEFAULT_UPDATE_GROUP_FROM_CHILD_DELAY = 0.5


class ZHAEntity(LogMixin, entity.Entity):
    """ZHA eitity."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    remove_future: asyncio.Future[Any]

    def __init__(self, entity_data: EntityData, *args, **kwargs) -> None:
        """Init ZHA entity."""
        super().__init__(*args, **kwargs)
        self.entity_data: EntityData = entity_data
        self._unsubs: list[Callable[[], None]] = []
        if (
            hasattr(self.entity_data.entity, "_attr_translation_key")
            and self.entity_data.entity._attr_translation_key is not None  # noqa: SLF001
        ):
            self._attr_translation_key = self.entity_data.entity._attr_translation_key  # noqa: SLF001
        if (
            hasattr(self.entity_data.entity, "_attr_entity_category")
            and self.entity_data.entity._attr_entity_category is not None  # noqa: SLF001
        ):
            if (
                self.entity_data.entity._attr_entity_category  # noqa: SLF001
                == ZHAEntityCategory.CONFIG
            ):
                self._attr_entity_category = EntityCategory.CONFIG
            elif (
                self.entity_data.entity._attr_entity_category  # noqa: SLF001
                == ZHAEntityCategory.DIAGNOSTIC
            ):
                self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if hasattr(self.entity_data.entity, "_attr_name"):
            self._attr_name = self.entity_data.entity._attr_name  # noqa: SLF001

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.entity_data.entity.unique_id

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return self.entity_data.device_proxy.device.available

    @property
    def device_info(self) -> DeviceInfo:
        """Return a device description for device registry."""
        zha_device_info = self.entity_data.device_proxy.device_info
        ieee = zha_device_info["ieee"]
        zha_gateway = self.entity_data.device_proxy.gateway_proxy.gateway

        return DeviceInfo(
            connections={(CONNECTION_ZIGBEE, ieee)},
            identifiers={(DOMAIN, ieee)},
            manufacturer=zha_device_info[ATTR_MANUFACTURER],
            model=zha_device_info[ATTR_MODEL],
            name=zha_device_info[ATTR_NAME],
            via_device=(DOMAIN, zha_gateway.state.node_info.ieee),
        )

    @callback
    def _handle_entity_events(self, event: Any) -> None:
        """Entity state changed."""
        self.debug("Handling event from entity: %s", event)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Run when about to be added to hass."""
        self.remove_future = self.hass.loop.create_future()
        self._unsubs.append(
            self.entity_data.entity.on_all_events(self._handle_entity_events)
        )
        remove_signal = (
            f"{SIGNAL_REMOVE_ENTITIES}_group_{self.entity_data.group_proxy.group.group_id}"
            if self.entity_data.is_group_entity
            and self.entity_data.group_proxy is not None
            else f"{SIGNAL_REMOVE_ENTITIES}_{self.entity_data.device_proxy.device.ieee}"
        )
        self._unsubs.append(
            async_dispatcher_connect(
                self.hass,
                remove_signal,
                functools.partial(self.async_remove, force_remove=True),
            )
        )
        self.entity_data.device_proxy.gateway_proxy.register_entity_reference(
            self.entity_id,
            self.entity_data,
            self.device_info,
            self.remove_future,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Disconnect entity object when removed."""
        for unsub in self._unsubs[:]:
            unsub()
            self._unsubs.remove(unsub)
        await super().async_will_remove_from_hass()
        self.remove_future.set_result(True)

    async def async_update(self) -> None:
        """Update the entity."""
        await self.entity_data.entity.async_update()
        self.async_write_ha_state()

    def log(self, level: int, msg: str, *args, **kwargs):
        """Log a message."""
        msg = f"%s: {msg}"
        args = (self.entity_id, *args)
        _LOGGER.log(level, msg, *args, **kwargs)
