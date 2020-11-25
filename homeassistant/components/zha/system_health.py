"""Provide info to system health."""
from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .core.const import DATA_ZHA, DATA_ZHA_GATEWAY


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register system health callbacks."""
    register.async_register_info(system_health_info, "/config/zha/dashboard")


async def system_health_info(hass):
    """Get info for the info page."""
    return {
        "network_active": hass.data[DATA_ZHA][
            DATA_ZHA_GATEWAY
        ].application_controller.is_controller_running
    }
