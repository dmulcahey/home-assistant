"""The ZHAWS integration."""
from __future__ import annotations

from zhaws.client.controller import Controller
from zhaws.client.device import Device

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import CONNECTION_ZIGBEE

from .const import COORDINATOR_IEEE, DOMAIN

"""
    Platform.ALARM_CONTROL_PANEL,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.DEVICE_TRACKER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SIREN,
    """
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]


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

    devices: dict[str, Device] = controller.devices

    for ieee, device in devices.items():
        if device.device.nwk == "0x0000":
            hass.data[DOMAIN][COORDINATOR_IEEE] = ieee
            device_registry = await hass.helpers.device_registry.async_get_registry()
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                connections={(CONNECTION_ZIGBEE, ieee)},
                identifiers={(DOMAIN, ieee)},
                name="Zigbee Coordinator",
                manufacturer="ZHAWS",
            )

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    await controller.clients.listen()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
