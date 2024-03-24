"""Representation of ZHA updates."""

from __future__ import annotations

from functools import cached_property
import logging
from typing import Any

from zigpy.application import ControllerApplication

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from . import EntityData
from .entity import ZHAEntity
from .helpers import get_zha_data, get_zha_gateway

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zigbee Home Automation update from config entry."""
    zha_data = get_zha_data(hass)
    if zha_data.update_coordinator is None:
        zha_data.update_coordinator = ZHAFirmwareUpdateCoordinator(
            hass, get_zha_gateway(hass).application_controller
        )
    entities_to_create = zha_data.platforms.pop(Platform.UPDATE, [])
    async_add_entities(entities_to_create)

    zha_data = get_zha_data(hass)
    entities_to_create = zha_data.platforms[Platform.UPDATE]


class ZHAFirmwareUpdateCoordinator(DataUpdateCoordinator[None]):  # pylint: disable=hass-enforce-coordinator-module
    """Firmware update coordinator that broadcasts updates network-wide."""

    def __init__(
        self, hass: HomeAssistant, controller_application: ControllerApplication
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="ZHA firmware update coordinator",
            update_method=self.async_update_data,
        )
        self.controller_application = controller_application

    async def async_update_data(self) -> None:
        """Fetch the latest firmware update data."""
        # Broadcast to all devices
        await self.controller_application.ota.broadcast_notify(jitter=100)


class ZHAFirmwareUpdateEntity(
    ZHAEntity, CoordinatorEntity[ZHAFirmwareUpdateCoordinator], UpdateEntity
):
    """Representation of a ZHA firmware update entity."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.SPECIFIC_VERSION
    )

    def __init__(self, entity_data: EntityData, **kwargs: Any) -> None:
        """Initialize the ZHA siren."""
        super().__init__(entity_data, **kwargs)
        zha_data = get_zha_data(entity_data.device_proxy.gateway_proxy.hass)
        CoordinatorEntity.__init__(self, zha_data.update_coordinator)

    @cached_property
    def installed_version(self) -> str | None:
        """Version installed and in use."""
        return self.entity_data.entity._attr_installed_version

    @cached_property
    def in_progress(self) -> bool | int | None:
        """Update installation progress.

        Needs UpdateEntityFeature.PROGRESS flag to be set for it to be used.

        Can either return a boolean (True if in progress, False if not)
        or an integer to indicate the progress in from 0 to 100%.
        """
        return self.entity_data.entity.in_progress

    @cached_property
    def latest_version(self) -> str | None:
        """Latest version available for install."""
        return self.entity_data.entity.latest_version

    @cached_property
    def release_summary(self) -> str | None:
        """Summary of the release notes or changelog.

        This is not suitable for long changelogs, but merely suitable
        for a short excerpt update description of max 255 characters.
        """
        return self.entity_data.entity.release_summary

    @cached_property
    def release_url(self) -> str | None:
        """URL to the full release notes of the latest version available."""
        return self.entity_data.entity.release_url

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install an update."""
        await self.entity_data.entity.async_install(version, backup, **kwargs)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update the entity."""
        await CoordinatorEntity.async_update(self)
        await super().async_update()
