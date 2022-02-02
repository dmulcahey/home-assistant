"""Config flow for ZHAWS integration."""
from __future__ import annotations

from abc import abstractmethod
import asyncio
import logging
from typing import Any

import voluptuous as vol
from zhaws.client.controller import Controller

from homeassistant import config_entries
from homeassistant.components import usb
from homeassistant.components.hassio import is_hassio
from homeassistant.components.hassio.discovery import HassioServiceInfo
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow, FlowManager, FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .addon import AddonError, AddonInfo, AddonManager, AddonState, get_addon_manager
from .const import (
    CONF_ADDON_DEVICE,
    CONF_INTEGRATION_CREATED_ADDON,
    CONF_USB_PATH,
    CONF_USE_ADDON,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_URL = "ws://localhost:8001"
ADDON_SETUP_TIMEOUT = 5
ADDON_SETUP_TIMEOUT_ROUNDS = 4
CONF_LOG_LEVEL = "log_level"
CONNECT_TIMEOUT = 10
TITLE = "ZHAWS"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
    }
)

ON_SUPERVISOR_SCHEMA = vol.Schema({vol.Optional(CONF_USE_ADDON, default=True): bool})


def get_manual_schema(user_input: dict[str, Any]) -> vol.Schema:
    """Return a schema for the manual step."""
    default_url = user_input.get(CONF_URL, DEFAULT_URL)
    return vol.Schema({vol.Required(CONF_URL, default=default_url): str})


def get_on_supervisor_schema(user_input: dict[str, Any]) -> vol.Schema:
    """Return a schema for the on Supervisor step."""
    default_use_addon = user_input[CONF_USE_ADDON]
    return vol.Schema({vol.Optional(CONF_USE_ADDON, default=default_use_addon): bool})


async def validate_input(hass: HomeAssistant, user_input: dict) -> dict:
    """Validate if the user input allows us to connect."""
    ws_address = user_input[CONF_URL]

    if not ws_address.startswith(("ws://", "wss://")):
        raise InvalidInput("invalid_ws_url")

    try:
        await validate_connectivity(hass, user_input)
    except CannotConnect as err:
        raise InvalidInput("cannot_connect") from err

    return {"title": "ZHAWS"}


async def validate_connectivity(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    session = async_get_clientsession(hass)
    controller: Controller = Controller(data[CONF_URL], session)

    try:
        await controller.connect()
    except Exception as err:
        raise CannotConnect(f"Unable to connect to ZHAWS: {err}") from err

    await controller.disconnect()


class BaseFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZHAWS."""

    VERSION = 1

    def __init__(self) -> None:
        """Set up flow instance."""
        self.usb_path: str | None = None
        self.ws_address: str | None = None
        self.restart_addon: bool = False
        # If we install the add-on we should uninstall it on entry remove.
        self.integration_created_addon = False
        self.install_task: asyncio.Task | None = None
        self.start_task: asyncio.Task | None = None

    @property
    @abstractmethod
    def flow_manager(self) -> FlowManager:
        """Return the flow manager of the flow."""

    async def async_step_install_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Install ZHAWS add-on."""
        if not self.install_task:
            self.install_task = self.hass.async_create_task(self._async_install_addon())
            return self.async_show_progress(
                step_id="install_addon", progress_action="install_addon"
            )

        try:
            await self.install_task
        except AddonError as err:
            self.install_task = None
            _LOGGER.error(err)
            return self.async_show_progress_done(next_step_id="install_failed")

        self.integration_created_addon = True
        self.install_task = None

        return self.async_show_progress_done(next_step_id="configure_addon")

    async def async_step_install_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add-on installation failed."""
        return self.async_abort(reason="addon_install_failed")

    async def async_step_start_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Start ZHAWS add-on."""
        if not self.start_task:
            self.start_task = self.hass.async_create_task(self._async_start_addon())
            return self.async_show_progress(
                step_id="start_addon", progress_action="start_addon"
            )

        try:
            await self.start_task
        except (CannotConnect, AddonError, AbortFlow) as err:
            self.start_task = None
            _LOGGER.error(err)
            return self.async_show_progress_done(next_step_id="start_failed")

        self.start_task = None
        return self.async_show_progress_done(next_step_id="finish_addon_setup")

    async def async_step_start_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add-on start failed."""
        return self.async_abort(reason="addon_start_failed")

    async def _async_start_addon(self) -> None:
        """Start the ZHAWS add-on."""
        addon_manager: AddonManager = get_addon_manager(self.hass)
        try:
            if self.restart_addon:
                await addon_manager.async_schedule_restart_addon()
            else:
                await addon_manager.async_schedule_start_addon()
            # Sleep some seconds to let the add-on start properly before connecting.
            for _ in range(ADDON_SETUP_TIMEOUT_ROUNDS):
                await asyncio.sleep(ADDON_SETUP_TIMEOUT)
                try:
                    if not self.ws_address:
                        discovery_info = await self._async_get_addon_discovery_info()
                        self.ws_address = (
                            f"ws://{discovery_info['host']}:{discovery_info['port']}"
                        )
                    await validate_connectivity(self.hass, {CONF_URL: self.ws_address})
                except (AbortFlow, CannotConnect) as err:
                    _LOGGER.debug(
                        "Add-on not ready yet, waiting %s seconds: %s",
                        ADDON_SETUP_TIMEOUT,
                        err,
                    )
                else:
                    break
            else:
                raise CannotConnect("Failed to start ZHAWS add-on: timeout")
        finally:
            # Continue the flow after show progress when the task is done.
            self.hass.async_create_task(
                self.flow_manager.async_configure(flow_id=self.flow_id)
            )

    @abstractmethod
    async def async_step_configure_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for config for ZHAWS add-on."""

    @abstractmethod
    async def async_step_finish_addon_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prepare info needed to complete the config entry.

        Get add-on discovery info and server version info.
        Set unique id and abort if already configured.
        """

    async def _async_get_addon_info(self) -> AddonInfo:
        """Return and cache ZHAWS add-on info."""
        addon_manager: AddonManager = get_addon_manager(self.hass)
        try:
            addon_info: AddonInfo = await addon_manager.async_get_addon_info()
        except AddonError as err:
            _LOGGER.error(err)
            raise AbortFlow("addon_info_failed") from err

        return addon_info

    async def _async_set_addon_config(self, config: dict) -> None:
        """Set ZHAWS add-on config."""
        addon_manager: AddonManager = get_addon_manager(self.hass)
        try:
            await addon_manager.async_set_addon_options(config)
        except AddonError as err:
            _LOGGER.error(err)
            raise AbortFlow("addon_set_config_failed") from err

    async def _async_install_addon(self) -> None:
        """Install the ZHAWS add-on."""
        addon_manager: AddonManager = get_addon_manager(self.hass)
        try:
            await addon_manager.async_schedule_install_addon()
        finally:
            # Continue the flow after show progress when the task is done.
            self.hass.async_create_task(
                self.flow_manager.async_configure(flow_id=self.flow_id)
            )

    async def _async_get_addon_discovery_info(self) -> dict:
        """Return add-on discovery info."""
        addon_manager: AddonManager = get_addon_manager(self.hass)
        try:
            discovery_info_config = await addon_manager.async_get_addon_discovery_info()
        except AddonError as err:
            _LOGGER.error(err)
            raise AbortFlow("addon_get_discovery_info_failed") from err

        return discovery_info_config


class ConfigFlow(BaseFlow, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZHAWS."""

    VERSION = 1

    def __init__(self) -> None:
        """Set up flow instance."""
        super().__init__()
        self.use_addon = False
        self._title: str | None = None
        self._usb_discovery = False

    @property
    def flow_manager(self) -> config_entries.ConfigEntriesFlowManager:
        """Return the correct flow manager."""
        return self.hass.config_entries.flow

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if is_hassio(self.hass):
            return await self.async_step_on_supervisor()

        return await self.async_step_manual()

    async def async_step_usb(self, discovery_info: usb.UsbServiceInfo) -> FlowResult:
        """Handle USB Discovery."""
        if not is_hassio(self.hass):
            return self.async_abort(reason="discovery_requires_supervisor")
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if self._async_in_progress():
            return self.async_abort(reason="already_in_progress")

        vid = discovery_info.vid
        pid = discovery_info.pid
        serial_number = discovery_info.serial_number
        device = discovery_info.device
        manufacturer = discovery_info.manufacturer
        description = discovery_info.description
        dev_path = await self.hass.async_add_executor_job(usb.get_serial_by_id, device)
        unique_id = f"{vid}:{pid}_{serial_number}_{manufacturer}_{description}"

        addon_info = await self._async_get_addon_info()
        if addon_info.state not in (AddonState.NOT_INSTALLED, AddonState.NOT_RUNNING):
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        self.usb_path = dev_path
        self._title = usb.human_readable_device_name(
            dev_path,
            serial_number,
            manufacturer,
            description,
            vid,
            pid,
        )
        self.context["title_placeholders"] = {CONF_NAME: self._title}
        return await self.async_step_usb_confirm()

    async def async_step_usb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle USB Discovery confirmation."""
        if user_input is None:
            return self.async_show_form(
                step_id="usb_confirm",
                description_placeholders={CONF_NAME: self._title},
                data_schema=vol.Schema({}),
            )

        self._usb_discovery = True

        return await self.async_step_on_supervisor({CONF_USE_ADDON: True})

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a manual configuration."""
        if user_input is None:
            return self.async_show_form(
                step_id="manual", data_schema=get_manual_schema({})
            )

        errors = {}

        try:
            await validate_input(self.hass, user_input)
        except InvalidInput as err:
            errors["base"] = err.error
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            await self.async_set_unique_id(
                user_input[CONF_URL], raise_on_progress=False
            )
            # Make sure we disable any add-on handling
            # if the controller is reconfigured in a manual step.
            self._abort_if_unique_id_configured(
                updates={
                    **user_input,
                    CONF_USE_ADDON: False,
                    CONF_INTEGRATION_CREATED_ADDON: False,
                }
            )
            self.ws_address = user_input[CONF_URL]
            return self._async_create_entry_from_vars()

        return self.async_show_form(
            step_id="manual", data_schema=get_manual_schema(user_input), errors=errors
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> FlowResult:
        """Receive configuration from add-on discovery info.

        This flow is triggered by the ZHAWS add-on.
        """
        if self._async_in_progress():
            return self.async_abort(reason="already_in_progress")

        self.ws_address = (
            f"ws://{discovery_info.config['host']}:{discovery_info.config['port']}"
        )
        try:
            await validate_connectivity(self.hass, {CONF_URL: self.ws_address})
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(
            f"ws://{discovery_info.config['host']}:{discovery_info.config['port']}"
        )
        self._abort_if_unique_id_configured(updates={CONF_URL: self.ws_address})

        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm the add-on discovery."""
        if user_input is not None:
            return await self.async_step_on_supervisor(
                user_input={CONF_USE_ADDON: True}
            )

        return self.async_show_form(step_id="hassio_confirm")

    async def async_step_on_supervisor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle logic when on Supervisor host."""
        if user_input is None:
            return self.async_show_form(
                step_id="on_supervisor", data_schema=ON_SUPERVISOR_SCHEMA
            )
        if not user_input[CONF_USE_ADDON]:
            return await self.async_step_manual()

        self.use_addon = True

        addon_info = await self._async_get_addon_info()

        if addon_info.state == AddonState.RUNNING:
            addon_config = addon_info.options
            self.usb_path = addon_config[CONF_ADDON_DEVICE]
            return await self.async_step_finish_addon_setup()

        if addon_info.state == AddonState.NOT_RUNNING:
            return await self.async_step_configure_addon()

        return await self.async_step_install_addon()

    async def async_step_configure_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for config for ZHAWS add-on."""
        addon_info = await self._async_get_addon_info()
        addon_config = addon_info.options

        if user_input is not None:
            if not self._usb_discovery:
                self.usb_path = user_input[CONF_USB_PATH]

            new_addon_config = {
                **addon_config,
                CONF_ADDON_DEVICE: self.usb_path,
            }

            if new_addon_config != addon_config:
                await self._async_set_addon_config(new_addon_config)

            return await self.async_step_start_addon()

        usb_path = self.usb_path or addon_config.get(CONF_ADDON_DEVICE) or ""

        schema: dict = {}

        if not self._usb_discovery:
            schema = {vol.Required(CONF_USB_PATH, default=usb_path): str, **schema}

        data_schema = vol.Schema(schema)

        return self.async_show_form(step_id="configure_addon", data_schema=data_schema)

    async def async_step_finish_addon_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prepare info needed to complete the config entry.

        Get add-on discovery info and server version info.
        Set unique id and abort if already configured.
        """
        if not self.ws_address:
            discovery_info = await self._async_get_addon_discovery_info()
            self.ws_address = f"ws://{discovery_info['host']}:{discovery_info['port']}"

        if not self.unique_id or self.context["source"] == config_entries.SOURCE_USB:
            try:
                await validate_connectivity(self.hass, {CONF_URL: self.ws_address})
            except CannotConnect as err:
                raise AbortFlow("cannot_connect") from err

            await self.async_set_unique_id(
                f"ws://{discovery_info['host']}:{discovery_info['port']}",
                raise_on_progress=False,
            )

        self._abort_if_unique_id_configured(
            updates={
                CONF_URL: self.ws_address,
                CONF_USB_PATH: self.usb_path,
            }
        )
        return self._async_create_entry_from_vars()

    @callback
    def _async_create_entry_from_vars(self) -> FlowResult:
        """Return a config entry for the flow."""
        # Abort any other flows that may be in progress
        for progress in self._async_in_progress():
            self.hass.config_entries.flow.async_abort(progress["flow_id"])

        return self.async_create_entry(
            title=TITLE,
            data={
                CONF_URL: self.ws_address,
                CONF_USB_PATH: self.usb_path,
                CONF_USE_ADDON: self.use_addon,
                CONF_INTEGRATION_CREATED_ADDON: self.integration_created_addon,
            },
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidInput(HomeAssistantError):
    """Error to indicate input data is invalid."""

    def __init__(self, error: str) -> None:
        """Initialize error."""
        super().__init__()
        self.error = error
