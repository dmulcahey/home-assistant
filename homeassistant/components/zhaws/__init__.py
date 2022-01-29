"""The ZHAWS integration."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import TypeVar

from zhaws.client.controller import Controller
from zhaws.client.model.events import DeviceFullyInitializedEvent, DeviceRemovedEvent
from zhaws.client.proxy import DeviceProxy, GroupProxy

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import CONNECTION_ZIGBEE, async_get_registry
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COORDINATOR_IEEE, DOMAIN, SIGNAL_ADD_ENTITIES
from .entity import ZhaEntity

# Platform.CLIMATE,
# Platform.FAN,
PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
    Platform.COVER,
    Platform.DEVICE_TRACKER,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
]


CALLABLE_T = TypeVar("CALLABLE_T", bound=Callable)  # pylint: disable=invalid-name

_LOGGER = logging.getLogger(__name__)


class EntityClassRegistry(dict):
    """Dict Registry of class name to class."""

    def register(
        self, platform: str, alternate_class_names: list[str] = None
    ) -> Callable[[CALLABLE_T], CALLABLE_T]:
        """Return decorator to register item with a specific name."""

        def decorator(entity_class: CALLABLE_T) -> CALLABLE_T:
            """Register decorated channel or item."""
            if platform not in self:
                self[platform] = {}
            self[platform][entity_class.__name__] = entity_class
            if alternate_class_names:
                for name in alternate_class_names:
                    self[platform][name] = entity_class
            return entity_class

        return decorator


ENTITY_CLASS_REGISTRY = EntityClassRegistry()


@callback
async def add_entities(
    async_add_entities: AddEntitiesCallback,
    platform: Platform,
    logger: logging.Logger,
    devices: list[DeviceProxy] | None,
    groups: list[GroupProxy] | None,
) -> None:
    """Set up the zhaws sensors from config entry."""
    logger.warning("Adding entities for platform: %s", platform)
    entities: list[ZhaEntity] = []
    if devices:
        for device in devices:
            for entity in device.device_model.entities.values():
                logger.debug("processed entity: %s", entity)
                if entity.platform != platform:
                    continue
                entity_class = ENTITY_CLASS_REGISTRY[platform][entity.class_name]
                logger.warning(
                    "Creating entity: %s with class: %s", entity, entity_class.__name__
                )
                entities.append(entity_class(device, entity))
    if groups:
        for group in groups:
            for entity in group.group_model.entities.values():
                logger.debug("processed entity: %s", entity)
                if entity.platform != platform:
                    continue
                entity_class = ENTITY_CLASS_REGISTRY[platform][entity.class_name]
                logger.warning(
                    "Creating entity: %s with class: %s", entity, entity_class.__name__
                )
                entities.append(entity_class(group, entity))

    async_add_entities(entities)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZHAWS from a config entry."""

    session = async_get_clientsession(hass)
    controller: Controller = None

    hass.data[DOMAIN] = {}
    hass.data[DOMAIN][entry.entry_id] = controller = Controller(
        entry.data["url"], session
    )

    await controller.connect()
    await controller.load_devices()
    await controller.load_groups()

    devices: dict[str, DeviceProxy] = controller.devices
    groups: dict[int, GroupProxy] = controller.groups

    for ieee, device in devices.items():
        if device.device_model.nwk == "0x0000":
            hass.data[DOMAIN][COORDINATOR_IEEE] = ieee
            device_registry = await hass.helpers.device_registry.async_get_registry()
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                connections={(CONNECTION_ZIGBEE, ieee)},
                identifiers={(DOMAIN, ieee)},
                name="Zigbee Coordinator",
                manufacturer="ZHAWS",
            )

    platform_tasks = []
    for platform in PLATFORMS:
        coro = hass.config_entries.async_forward_entry_setup(entry, platform)
        platform_tasks.append(hass.async_create_task(coro))
    results = await asyncio.gather(*platform_tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            _LOGGER.warning("Couldn't setup zhaws platform: %s", res)

    async_dispatcher_send(hass, SIGNAL_ADD_ENTITIES, devices.values(), groups.values())

    @callback
    def add_entities_new_join(event: DeviceFullyInitializedEvent):
        """Add entities from ZHAWS."""
        if event.new_join:
            _LOGGER.info("New device joined: %s - adding entities", event.device)
            async_dispatcher_send(
                hass, SIGNAL_ADD_ENTITIES, [controller.devices[event.device.ieee]], []
            )

    controller.on_event("device_fully_initialized", add_entities_new_join)

    ha_device_registry = await async_get_registry(hass)

    @callback
    def remove_device(event: DeviceRemovedEvent):
        """Remove a device from ZHAWS."""
        async_dispatcher_send(hass, f"remove_device_{str(event.device.ieee)}")
        reg_device = ha_device_registry.async_get_device({("zhaws", event.device.ieee)})
        if reg_device is not None:
            _LOGGER.info("Removing device: %s from device registry", event.device)
            ha_device_registry.async_remove_device(reg_device.id)

    controller.on_event("device_removed", remove_device)

    await controller.clients.listen()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
