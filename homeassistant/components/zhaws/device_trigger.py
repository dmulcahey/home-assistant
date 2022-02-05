"""Provides device automations for ZHA devices that emit events."""
import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.device_automation.exceptions import (
    InvalidDeviceAutomationConfig,
)
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE

from . import DOMAIN, async_get_zhaws_device

CONF_SUBTYPE = "subtype"
DEVICE = "device"
DEVICE_IEEE = "device_ieee"
ZHA_EVENT = "zha_event"

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): str, vol.Required(CONF_SUBTYPE): str}
)


async def async_validate_trigger_config(hass, config):
    """Validate config."""
    config = TRIGGER_SCHEMA(config)

    if "zhaws" in hass.config.components:
        trigger = (config[CONF_TYPE], config[CONF_SUBTYPE])
        try:
            device = await async_get_zhaws_device(hass, config[CONF_DEVICE_ID])
        except (KeyError, AttributeError) as err:
            raise InvalidDeviceAutomationConfig from err
        if (
            device.device_automation_triggers is None
            or trigger not in device.device_automation_triggers
        ):
            raise InvalidDeviceAutomationConfig

    return config


async def async_attach_trigger(hass, config, action, automation_info):
    """Listen for state changes based on configuration."""
    trigger = (config[CONF_TYPE], config[CONF_SUBTYPE])
    try:
        device = await async_get_zhaws_device(hass, config[CONF_DEVICE_ID])
    except (KeyError, AttributeError):
        return None

    if trigger not in device.device_automation_triggers:
        return None

    trigger = device.device_automation_triggers[trigger]

    event_config = {
        event_trigger.CONF_PLATFORM: "event",
        event_trigger.CONF_EVENT_TYPE: ZHA_EVENT,
        event_trigger.CONF_EVENT_DATA: {
            DEVICE_IEEE: device.device_model.ieee,
            **trigger,
        },
    }

    event_config = event_trigger.TRIGGER_SCHEMA(event_config)
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, automation_info, platform_type="device"
    )


async def async_get_triggers(hass, device_id):
    """List device triggers.

    Make sure the device supports device automations and
    if it does return the trigger list.
    """
    device = await async_get_zhaws_device(hass, device_id)

    if not device.device_automation_triggers:
        return

    triggers = []
    for trigger, subtype in device.device_automation_triggers.keys():
        triggers.append(
            {
                CONF_DEVICE_ID: device_id,
                CONF_DOMAIN: DOMAIN,
                CONF_PLATFORM: DEVICE,
                CONF_TYPE: trigger,
                CONF_SUBTYPE: subtype,
            }
        )

    return triggers
