"""Flo device object."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from typing import Any, override

from aioflo.api import API
from aioflo.errors import RequestError
from orjson import JSONDecodeError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, IMPLAUSIBLE_WATER_TEMP_F, LOGGER

type FloConfigEntry = ConfigEntry[FloRuntimeData]


@dataclass
class FloRuntimeData:
    """Flo runtime data."""

    client: API
    devices: list[FloDeviceDataUpdateCoordinator]


class FloDeviceDataUpdateCoordinator(DataUpdateCoordinator[None]):
    """Flo device object."""

    config_entry: FloConfigEntry
    _failure_count: int = 0

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: FloConfigEntry,
        api_client: API,
        location_id: str,
        device_id: str,
    ) -> None:
        """Initialize the device."""
        self.hass: HomeAssistant = hass
        self.api_client: API = api_client
        self._flo_location_id: str = location_id
        self._flo_device_id: str = device_id
        self._manufacturer: str = "Flo by Moen"
        self._device_information: dict[str, Any] = {}
        self._water_usage: dict[str, Any] = {}
        self._consumption_day: tuple[date, str] | None = None
        self._consumption_success = False
        self._consumption_listener: Callable[[], None] | None = None
        self._consumption_task: asyncio.Task[None] | None = None
        self._unsub_midnight: Callable[[], None] | None = None
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}-{device_id}",
            update_interval=timedelta(seconds=60),
        )

    @override
    async def _async_update_data(self) -> None:
        """Update data via library."""
        self._async_request_consumption()
        try:
            async with asyncio.timeout(20):
                await self.send_presence_ping()
                await self._update_device()
                self._failure_count = 0
        except (RequestError, TimeoutError, JSONDecodeError) as error:
            self._failure_count += 1
            if not self._device_information or self._failure_count > 3:
                raise UpdateFailed(error) from error

    @property
    def location_id(self) -> str:
        """Return Flo location id."""
        return self._flo_location_id

    @property
    def id(self) -> str:
        """Return Flo device id."""
        return self._flo_device_id

    @property
    def device_name(self) -> str:
        """Return device name."""
        return self._device_information.get(
            "nickname", f"{self.manufacturer} {self.model}"
        )

    @property
    def manufacturer(self) -> str:
        """Return manufacturer for device."""
        return self._manufacturer

    @property
    def mac_address(self) -> str:
        """Return ieee address for device."""
        return self._device_information["macAddress"]

    @property
    def model(self) -> str:
        """Return model for device."""
        return self._device_information["deviceModel"]

    @property
    def rssi(self) -> float:
        """Return rssi for device."""
        return self._device_information["connectivity"]["rssi"]

    @property
    def last_heard_from_time(self) -> str:
        """Return lastHeardFromTime for device."""
        return self._device_information["lastHeardFromTime"]

    @property
    def device_type(self) -> str:
        """Return the device type for the device."""
        return self._device_information["deviceType"]

    @property
    def available(self) -> bool:
        """Return True if device is available."""
        return self.last_update_success and self._device_information["isConnected"]

    @property
    def current_system_mode(self) -> str:
        """Return the current system mode."""
        return self._device_information["systemMode"]["lastKnown"]

    @property
    def target_system_mode(self) -> str:
        """Return the target system mode."""
        return self._device_information["systemMode"]["target"]

    @property
    def current_flow_rate(self) -> float:
        """Return current flow rate in gpm."""
        return self._device_information["telemetry"]["current"]["gpm"]

    @property
    def current_psi(self) -> float:
        """Return the current pressure in psi."""
        return self._device_information["telemetry"]["current"]["psi"]

    @property
    def temperature(self) -> float | None:
        """Return the current temperature in degrees F, or None if not measured."""
        temperature = self._device_information["telemetry"]["current"]["tempF"]
        if temperature is None or temperature >= IMPLAUSIBLE_WATER_TEMP_F:
            return None
        return temperature

    @property
    def humidity(self) -> float:
        """Return the current humidity in percent (0-100)."""
        return self._device_information["telemetry"]["current"]["humidity"]

    @property
    def consumption_today(self) -> float | None:
        """Return the current consumption for today in gallons."""
        if not self.consumption_available:
            return None
        return self._water_usage["aggregations"]["sumTotalGallonsConsumed"]

    @property
    def consumption_available(self) -> bool:
        """Return whether consumption succeeded for the current local day."""
        return self._consumption_success and self._consumption_day == (
            dt_util.now().date(),
            self.hass.config.time_zone,
        )

    @property
    def firmware_version(self) -> str:
        """Return the firmware version for the device."""
        return self._device_information["fwVersion"]

    @property
    def serial_number(self) -> str | None:
        """Return the serial number for the device."""
        return self._device_information.get("serialNumber")

    @property
    def pending_info_alerts_count(self) -> int:
        """Return the number of pending info alerts for the device."""
        return self._device_information["notifications"]["pending"]["infoCount"]

    @property
    def pending_warning_alerts_count(self) -> int:
        """Return the number of pending warning alerts for the device."""
        return self._device_information["notifications"]["pending"]["warningCount"]

    @property
    def pending_critical_alerts_count(self) -> int:
        """Return the number of pending critical alerts for the device."""
        return self._device_information["notifications"]["pending"]["criticalCount"]

    @property
    def has_alerts(self) -> bool:
        """Return True if any alert counts are greater than zero."""
        return bool(
            self.pending_info_alerts_count
            or self.pending_warning_alerts_count
            or self.pending_critical_alerts_count
        )

    @property
    def water_detected(self) -> bool:
        """Return whether water is detected, for leak detectors."""
        return self._device_information["fwProperties"]["telemetry_water"]

    @property
    def last_known_valve_state(self) -> str:
        """Return the last known valve state for the device."""
        return self._device_information["valve"]["lastKnown"]

    @property
    def target_valve_state(self) -> str:
        """Return the target valve state for the device."""
        return self._device_information["valve"]["target"]

    @property
    def battery_level(self) -> float:
        """Return the battery level for battery-powered device, e.g. leak detectors."""
        return self._device_information["battery"]["level"]

    async def send_presence_ping(self):
        """Send Flo a presence ping."""
        await self.api_client.presence.ping()

    async def async_set_mode_home(self):
        """Set the Flo location to home mode."""
        await self.api_client.location.set_mode_home(self._flo_location_id)

    async def async_set_mode_away(self):
        """Set the Flo location to away mode."""
        await self.api_client.location.set_mode_away(self._flo_location_id)

    async def async_set_mode_sleep(self, sleep_minutes, revert_to_mode):
        """Set the Flo location to sleep mode."""
        await self.api_client.location.set_mode_sleep(
            self._flo_location_id, sleep_minutes, revert_to_mode
        )

    async def async_run_health_test(self):
        """Run a Flo device health test."""
        await self.api_client.device.run_health_test(self._flo_device_id)

    async def _update_device(self, *_) -> None:
        """Update the device information from the API."""
        self._device_information = await self.api_client.device.get_info(
            self._flo_device_id
        )
        LOGGER.debug("Flo device data: %s", self._device_information)

    @callback
    def async_start_consumption_updates(
        self, update_listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Enable optional updates while the daily usage sensor is present."""
        self._consumption_listener = update_listener
        unsub_config = self.hass.bus.async_listen(
            EVENT_CORE_CONFIG_UPDATE, self._async_config_updated
        )
        self._async_consumption_day_changed()
        self._async_request_consumption()

        @callback
        def stop() -> None:
            self._consumption_listener = None
            unsub_config()
            if self._unsub_midnight is not None:
                self._unsub_midnight()
            if self._consumption_task is not None:
                self._consumption_task.cancel()

        return stop

    @callback
    def _async_consumption_day_changed(self, now: datetime | None = None) -> None:
        """Publish stale availability immediately and schedule local midnight."""
        if self._consumption_listener is None:
            return
        if self._unsub_midnight is not None:
            self._unsub_midnight()
        self._unsub_midnight = async_track_point_in_time(
            self.hass,
            self._async_consumption_day_changed,
            dt_util.start_of_local_day(dt_util.now().date() + timedelta(days=1)),
        )
        self._consumption_listener()
        if not self.config_entry.pref_disable_polling:
            self._async_request_consumption()

    @callback
    def _async_config_updated(self, event: Event) -> None:
        """Reschedule the local-day boundary after a timezone change."""
        if "time_zone" in event.data:
            self._async_consumption_day_changed()

    @callback
    def _async_request_consumption(self) -> asyncio.Task[None] | None:
        """Share one optional request without delaying live telemetry."""
        if self._consumption_listener is None:
            return None
        if self._consumption_task is None or self._consumption_task.done():
            self._consumption_task = self.config_entry.async_create_background_task(
                self.hass,
                self._update_consumption_data(),
                f"{self.name} consumption",
                eager_start=False,
            )
        return self._consumption_task

    async def async_refresh_consumption(self) -> None:
        """Refresh live data and join its optional consumption request."""
        await self.async_request_refresh()
        if (task := self._consumption_task) is not None:
            await asyncio.shield(task)

    async def _update_consumption_data(self) -> None:
        """Keep consumption failures and last-good data separate from telemetry."""
        today = dt_util.now().date()
        time_zone = self.hass.config.time_zone
        start_date = datetime(today.year, today.month, today.day, 0, 0)
        end_date = datetime(today.year, today.month, today.day, 23, 59, 59, 999000)
        try:
            async with asyncio.timeout(20):
                usage = await self.api_client.water.get_consumption_info(
                    self._flo_location_id,
                    start_date,
                    end_date,
                    device_mac_address=self.mac_address,
                )
            gallons = usage["aggregations"]["sumTotalGallonsConsumed"]
        except (
            RequestError,
            TimeoutError,
            JSONDecodeError,
            KeyError,
            TypeError,
        ) as error:
            self._consumption_success = False
            LOGGER.debug("Error updating Flo consumption: %s", error)
        else:
            self._consumption_success = (
                not isinstance(gallons, bool)
                and isinstance(gallons, (int, float))
                and math.isfinite(gallons)
                and gallons >= 0
                and (today, time_zone)
                == (dt_util.now().date(), self.hass.config.time_zone)
            )
            if self._consumption_success:
                self._water_usage = usage
                self._consumption_day = (today, time_zone)
                LOGGER.debug("Updated Flo consumption data: %s", self._water_usage)
            else:
                LOGGER.debug("Invalid or stale Flo consumption data: %s", usage)
        if self._consumption_listener is not None:
            self._consumption_listener()
        if (today, time_zone) != (dt_util.now().date(), self.hass.config.time_zone):
            # Retry a request that finished after its local day became stale.
            self.hass.loop.call_soon(self._async_request_consumption)
