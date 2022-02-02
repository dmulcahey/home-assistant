"""Constants for the ZHAWS integration."""
import logging

COORDINATOR_IEEE = "coordinator_ieee"
DOMAIN = ZHAWS = "zhaws"
SIGNAL_ADD_ENTITIES = "zhaws_signal_add_entities"

CONF_ADDON_DEVICE = "device"
CONF_ADDON_LOG_LEVEL = "log_level"
CONF_USE_ADDON = "use_addon"
CONF_USB_PATH = "usb_path"
CONF_INTEGRATION_CREATED_ADDON = "integration_created_addon"
LOGGER = logging.getLogger(__package__)
ADDON_SLUG = "core_zwave_js"
