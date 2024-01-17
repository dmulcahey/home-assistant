"""Support for ZHA covers."""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Any, cast

from zigpy.zcl.clusters.closures import WindowCovering as WindowCoveringCluster
from zigpy.zcl.foundation import Status

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core import discovery
from .core.cluster_handlers.closures import WindowCovering
from .core.const import (
    CLUSTER_HANDLER_COVER,
    CLUSTER_HANDLER_LEVEL,
    CLUSTER_HANDLER_ON_OFF,
    CLUSTER_HANDLER_SHADE,
    SIGNAL_ADD_ENTITIES,
    SIGNAL_ATTR_UPDATED,
    SIGNAL_SET_LEVEL,
)
from .core.helpers import get_zha_data
from .core.registries import ZHA_ENTITIES
from .entity import ZhaEntity

if TYPE_CHECKING:
    from .core.cluster_handlers import ClusterHandler
    from .core.device import ZHADevice

_LOGGER = logging.getLogger(__name__)

MULTI_MATCH = functools.partial(ZHA_ENTITIES.multipass_match, Platform.COVER)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zigbee Home Automation cover from config entry."""
    zha_data = get_zha_data(hass)
    entities_to_create = zha_data.platforms[Platform.COVER]

    unsub = async_dispatcher_connect(
        hass,
        SIGNAL_ADD_ENTITIES,
        functools.partial(
            discovery.async_add_entities, async_add_entities, entities_to_create
        ),
    )
    config_entry.async_on_unload(unsub)


WCAttrs = WindowCoveringCluster.AttributeDefs
WCT = WindowCoveringCluster.WindowCoveringType

ZCL_TO_COVER_DEVICE_CLASS = {
    WCT.Awning: CoverDeviceClass.AWNING,
    WCT.Drapery: CoverDeviceClass.CURTAIN,
    WCT.Projector_screen: CoverDeviceClass.SHADE,
    WCT.Rollershade: CoverDeviceClass.SHADE,
    WCT.Rollershade_two_motors: CoverDeviceClass.SHADE,
    WCT.Rollershade_exterior: CoverDeviceClass.SHADE,
    WCT.Rollershade_exterior_two_motors: CoverDeviceClass.SHADE,
    WCT.Shutter: CoverDeviceClass.SHUTTER,
    WCT.Tilt_blind_tilt_only: CoverDeviceClass.BLIND,
    WCT.Tilt_blind_tilt_and_lift: CoverDeviceClass.BLIND,
}


@MULTI_MATCH(cluster_handler_names=CLUSTER_HANDLER_COVER)
class ZhaCover(ZhaEntity, CoverEntity):
    """Representation of a ZHA cover."""

    _attr_translation_key: str = "cover"

    def __init__(
        self,
        unique_id: str,
        zha_device: ZHADevice,
        cluster_handlers: list[ClusterHandler],
        **kwargs: Any,
    ) -> None:
        """Init this cover."""
        super().__init__(unique_id, zha_device, cluster_handlers, **kwargs)
        cluster_handler = self.cluster_handlers.get(CLUSTER_HANDLER_COVER)
        assert cluster_handler
        self._cover_cluster_handler: WindowCovering = cast(
            WindowCovering, cluster_handler
        )
        self._attr_device_class: CoverDeviceClass | None = (
            ZCL_TO_COVER_DEVICE_CLASS.get(
                self._cover_cluster_handler.window_covering_type
            )
        )
        self._attr_supported_features: CoverEntityFeature = (
            self._determine_supported_features()
        )
        self._current_position_lift_percentage: int | None = (
            self._cover_cluster_handler.current_position_lift_percentage
        )
        self._current_position_tilt_percentage: int | None = (
            self._cover_cluster_handler.current_position_tilt_percentage
        )
        self._determine_state()

    def _determine_supported_features(self) -> CoverEntityFeature:
        """Determine the supported cover features."""
        supported_features: CoverEntityFeature = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        if self._cover_cluster_handler.window_covering_type in (
            WCT.Shutter,
            WCT.Tilt_blind_tilt_only,
            WCT.Tilt_blind_tilt_and_lift,
        ):
            supported_features |= CoverEntityFeature.SET_TILT_POSITION
            supported_features |= CoverEntityFeature.OPEN_TILT
            supported_features |= CoverEntityFeature.CLOSE_TILT
            supported_features |= CoverEntityFeature.STOP_TILT
        return supported_features

    def _determine_state(self) -> None:
        """Determine the state of the cover.

        In HA None is unknown, 0 is closed, 100 is fully open.
        In ZCL 0 is fully open, 100 is fully closed.
        Keep in mind the values have already been flipped to match HA
        when the values were processed by async_set_position
        """
        if (
            self._cover_cluster_handler.inverted
            and self._current_position_lift_percentage == 0
        ):
            self._state = STATE_OPEN
        elif (
            self._cover_cluster_handler.inverted
            and self._current_position_lift_percentage == 100
        ):
            self._state = STATE_CLOSED
        elif self._current_position_lift_percentage == 100:
            self._state = STATE_OPEN
        elif self._current_position_lift_percentage == 0:
            self._state = STATE_CLOSED

    async def async_added_to_hass(self) -> None:
        """Run when the cover entity is about to be added to hass."""
        await super().async_added_to_hass()
        self.async_accept_signal(
            self._cover_cluster_handler, SIGNAL_ATTR_UPDATED, self.async_set_position
        )

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is closed.

        In HA None is unknown, 0 is closed, 100 is fully open.
        In ZCL 0 is fully open, 100 is fully closed.
        Keep in mind the values have already been flipped to match HA
        when the values were processed by async_set_position
        """
        if self.current_cover_position is None:
            return None
        if self._cover_cluster_handler.inverted:
            return self.current_cover_position == 100
        return self.current_cover_position == 0

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening or not."""
        return self._state == STATE_OPENING

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing or not."""
        return self._state == STATE_CLOSING

    @property
    def current_cover_position(self) -> int | None:
        """Return the current position of ZHA cover.

        In HA None is unknown, 0 is closed, 100 is fully open.
        In ZCL 0 is fully open, 100 is fully closed.
        Keep in mind the values have already been flipped to match HA
        when the values were processed by async_set_position
        """
        return self._current_position_lift_percentage

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return the current tilt position of the cover."""
        return self._current_position_tilt_percentage

    @callback
    def async_set_position(self, attr_id, attr_name, value):
        """Handle position update from cluster handler."""
        _LOGGER.debug(
            "setting position: attrid[%s] attr_name[%s] value[%s]",
            attr_id,
            attr_name,
            value,
        )
        # flip the values to match HA
        if attr_name == WCAttrs.current_position_lift_percentage.name:
            self._current_position_lift_percentage = 100 - value
            _LOGGER.debug(
                "position set to %s after adjusting for HA",
                self._current_position_lift_percentage,
            )
        elif attr_name == WCAttrs.current_position_tilt_percentage.name:
            self._current_position_tilt_percentage = 100 - value
            _LOGGER.debug(
                "tilt set to %s after adjusting for HA",
                self._current_position_tilt_percentage,
            )

        self._determine_state()
        self.async_write_ha_state()

    @callback
    def async_update_state(self, state):
        """Handle state update from HA operations below."""
        _LOGGER.debug("async_update_state=%s", state)
        self._state = state
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        res = await self._cover_cluster_handler.up_open()
        if res[1] is not Status.SUCCESS:
            raise HomeAssistantError(f"Failed to open cover: {res[1]}")
        self.async_update_state(STATE_OPENING)

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the cover tilt."""
        res = await self._cover_cluster_handler.go_to_tilt_percentage(0)
        if res[1] is not Status.SUCCESS:
            raise HomeAssistantError(f"Failed to open cover tilt: {res[1]}")
        self.async_update_state(STATE_OPENING)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        res = await self._cover_cluster_handler.down_close()
        if res[1] is not Status.SUCCESS:
            raise HomeAssistantError(f"Failed to close cover: {res[1]}")
        self.async_update_state(STATE_CLOSING)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the cover tilt."""
        res = await self._cover_cluster_handler.go_to_tilt_percentage(100)
        if res[1] is not Status.SUCCESS:
            raise HomeAssistantError(f"Failed to close cover tilt: {res[1]}")
        self.async_update_state(STATE_CLOSING)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        new_pos = kwargs[ATTR_POSITION]
        res = await self._cover_cluster_handler.go_to_lift_percentage(100 - new_pos)
        if res[1] is not Status.SUCCESS:
            raise HomeAssistantError(f"Failed to set cover position: {res[1]}")
        self.async_update_state(
            STATE_CLOSING
            if new_pos < self._current_position_lift_percentage
            else STATE_OPENING
        )

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Move the cover tilt to a specific position."""
        new_pos = kwargs[ATTR_TILT_POSITION]
        res = await self._cover_cluster_handler.go_to_tilt_percentage(100 - new_pos)
        if res[1] is not Status.SUCCESS:
            raise HomeAssistantError(f"Failed to set cover tilt position: {res[1]}")
        self.async_update_state(
            STATE_CLOSING
            if new_pos < self._current_position_tilt_percentage
            else STATE_OPENING
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        res = await self._cover_cluster_handler.stop()
        if res[1] is not Status.SUCCESS:
            raise HomeAssistantError(f"Failed to stop cover: {res[1]}")
        self._determine_state()
        self.async_write_ha_state()

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Stop the cover tilt."""
        await self.async_stop_cover()


@MULTI_MATCH(
    cluster_handler_names={
        CLUSTER_HANDLER_LEVEL,
        CLUSTER_HANDLER_ON_OFF,
        CLUSTER_HANDLER_SHADE,
    }
)
class Shade(ZhaEntity, CoverEntity):
    """ZHA Shade."""

    _attr_device_class = CoverDeviceClass.SHADE
    _attr_translation_key: str = "shade"

    def __init__(
        self,
        unique_id: str,
        zha_device: ZHADevice,
        cluster_handlers: list[ClusterHandler],
        **kwargs,
    ) -> None:
        """Initialize the ZHA light."""
        super().__init__(unique_id, zha_device, cluster_handlers, **kwargs)
        self._on_off_cluster_handler = self.cluster_handlers[CLUSTER_HANDLER_ON_OFF]
        self._level_cluster_handler = self.cluster_handlers[CLUSTER_HANDLER_LEVEL]
        self._position: int | None = None
        self._is_open: bool | None = None

    @property
    def current_cover_position(self) -> int | None:
        """Return current position of cover.

        None is unknown, 0 is closed, 100 is fully open.
        """
        return self._position

    @property
    def is_closed(self) -> bool | None:
        """Return True if shade is closed."""
        if self._is_open is None:
            return None
        return not self._is_open

    async def async_added_to_hass(self) -> None:
        """Run when about to be added to hass."""
        await super().async_added_to_hass()
        self.async_accept_signal(
            self._on_off_cluster_handler,
            SIGNAL_ATTR_UPDATED,
            self.async_set_open_closed,
        )
        self.async_accept_signal(
            self._level_cluster_handler, SIGNAL_SET_LEVEL, self.async_set_level
        )

    @callback
    def async_restore_last_state(self, last_state):
        """Restore previous state."""
        self._is_open = last_state.state == STATE_OPEN
        if ATTR_CURRENT_POSITION in last_state.attributes:
            self._position = last_state.attributes[ATTR_CURRENT_POSITION]

    @callback
    def async_set_open_closed(self, attr_id: int, attr_name: str, value: bool) -> None:
        """Set open/closed state."""
        self._is_open = bool(value)
        self.async_write_ha_state()

    @callback
    def async_set_level(self, value: int) -> None:
        """Set the reported position."""
        value = max(0, min(255, value))
        self._position = int(value * 100 / 255)
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the window cover."""
        res = await self._on_off_cluster_handler.on()
        if res[1] != Status.SUCCESS:
            raise HomeAssistantError(f"Failed to open cover: {res[1]}")

        self._is_open = True
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the window cover."""
        res = await self._on_off_cluster_handler.off()
        if res[1] != Status.SUCCESS:
            raise HomeAssistantError(f"Failed to close cover: {res[1]}")

        self._is_open = False
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the roller shutter to a specific position."""
        new_pos = kwargs[ATTR_POSITION]
        res = await self._level_cluster_handler.move_to_level_with_on_off(
            new_pos * 255 / 100, 1
        )

        if res[1] != Status.SUCCESS:
            raise HomeAssistantError(f"Failed to set cover position: {res[1]}")

        self._position = new_pos
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        res = await self._level_cluster_handler.stop()
        if res[1] != Status.SUCCESS:
            raise HomeAssistantError(f"Failed to stop cover: {res[1]}")


@MULTI_MATCH(
    cluster_handler_names={CLUSTER_HANDLER_LEVEL, CLUSTER_HANDLER_ON_OFF},
    manufacturers="Keen Home Inc",
)
class KeenVent(Shade):
    """Keen vent cover."""

    _attr_device_class = CoverDeviceClass.DAMPER
    _attr_translation_key: str = "keen_vent"

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        position = self._position or 100
        await asyncio.gather(
            self._level_cluster_handler.move_to_level_with_on_off(
                position * 255 / 100, 1
            ),
            self._on_off_cluster_handler.on(),
        )

        self._is_open = True
        self._position = position
        self.async_write_ha_state()
