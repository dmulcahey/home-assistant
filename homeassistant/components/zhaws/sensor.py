"""Sensors on Zigbee Home Automation networks."""
from __future__ import annotations

import functools
import logging

from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_BILLION,
    CONCENTRATION_PARTS_PER_MILLION,
    ELECTRIC_CURRENT_AMPERE,
    ELECTRIC_POTENTIAL_VOLT,
    ENTITY_CATEGORY_DIAGNOSTIC,
    LIGHT_LUX,
    PERCENTAGE,
    POWER_VOLT_AMPERE,
    POWER_WATT,
    PRESSURE_HPA,
    TEMP_CELSIUS,
    TIME_HOURS,
    TIME_SECONDS,
    VOLUME_FLOW_RATE_CUBIC_FEET_PER_MINUTE,
    VOLUME_FLOW_RATE_CUBIC_METERS_PER_HOUR,
    VOLUME_GALLONS,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import ENTITY_CLASS_REGISTRY, add_entities
from .const import SIGNAL_ADD_ENTITIES
from .entity import ZhaEntity

BATTERY_SIZE = "battery_size"
BATTERY_LEVEL = "battery_level"
BATTERY_VOLTAGE = "battery_voltage"

REGISTER_CLASS = functools.partial(ENTITY_CLASS_REGISTRY.register, Platform.SENSOR)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the zhaws sensors from config entry."""
    unsub = async_dispatcher_connect(
        hass,
        SIGNAL_ADD_ENTITIES,
        functools.partial(add_entities, async_add_entities, Platform.SENSOR, _LOGGER),
    )
    config_entry.async_on_unload(unsub)


class Sensor(ZhaEntity, SensorEntity):
    """Base ZHA sensor."""

    _unit: str | None = None

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        super().__init__(*args, **kwargs)
        self._state = None
        if type(self._platform_entity.state) in (int, float, bool, str):
            self._state = self._platform_entity.state
        if hasattr(self._platform_entity, "attribute"):
            self._sensor_attribute = self._platform_entity.attribute

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = event.state
        self.async_write_ha_state()

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement of this entity."""
        return self._unit

    @property
    def native_value(self) -> StateType:
        """Return the state of the entity."""
        return self._state


@REGISTER_CLASS()
class AnalogInput(Sensor):
    """Sensor that displays analog input values."""


@REGISTER_CLASS()
class Battery(Sensor):
    """Battery sensor of power configuration cluster."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.BATTERY
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        super().__init__(*args, **kwargs)
        self._state = self._platform_entity.state.state
        self._extra_state_attributes = {
            BATTERY_SIZE: self._platform_entity.state.battery_size,
            "battery_quantity": self._platform_entity.state.battery_quantity,
            BATTERY_VOLTAGE: self._platform_entity.state.battery_voltage,
        }

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = event.state["state"]
        self._extra_state_attributes = {}
        if BATTERY_SIZE in event.state:
            self._extra_state_attributes[BATTERY_SIZE] = event.state[BATTERY_SIZE]
        if "battery_quantity" in event.state:
            self._extra_state_attributes["battery_quantity"] = event.state[
                "battery_quantity"
            ]
        if BATTERY_VOLTAGE in event.state:
            self._extra_state_attributes[BATTERY_VOLTAGE] = event.state[BATTERY_VOLTAGE]
        self.async_write_ha_state()


@REGISTER_CLASS()
class ElectricalMeasurement(Sensor):
    """Active power measurement."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.POWER
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = POWER_WATT

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        super().__init__(*args, **kwargs)
        self._state = self._platform_entity.state.state
        self._max_attr_name = f"{self._sensor_attribute}_max"

        if hasattr(self._platform_entity.state, "measurement_type"):
            measurement_type = self._platform_entity.state.measurement_type
            if measurement_type is not None:
                measurement_type = measurement_type.title().replace("_", " ")
            self._extra_state_attributes = {"measurement_type": measurement_type}
            if hasattr(self._platform_entity.state, self._max_attr_name):
                self._extra_state_attributes[self._max_attr_name] = getattr(
                    self._platform_entity.state, self._max_attr_name
                )

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = event.state["state"]
        self._extra_state_attributes = {}
        if hasattr(self._platform_entity.state, "measurement_type"):
            measurement_type = self._platform_entity.state.measurement_type
            if measurement_type is not None:
                measurement_type = measurement_type.title().replace("_", " ")
            self._extra_state_attributes = {"measurement_type": measurement_type}
            if hasattr(self._platform_entity.state, self._max_attr_name):
                self._extra_state_attributes[self._max_attr_name] = getattr(
                    self._platform_entity.state, self._max_attr_name
                )
        self.async_write_ha_state()


@REGISTER_CLASS()
class ElectricalMeasurementApparentPower(ElectricalMeasurement):
    """Apparent power measurement."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.APPARENT_POWER
    _unit = POWER_VOLT_AMPERE


@REGISTER_CLASS()
class ElectricalMeasurementRMSCurrent(ElectricalMeasurement):
    """RMS current measurement."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.CURRENT
    _unit = ELECTRIC_CURRENT_AMPERE


@REGISTER_CLASS()
class ElectricalMeasurementRMSVoltage(ElectricalMeasurement):
    """RMS Voltage measurement."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.CURRENT
    _unit = ELECTRIC_POTENTIAL_VOLT


@REGISTER_CLASS()
class Humidity(Sensor):
    """Humidity sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.HUMIDITY
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PERCENTAGE


@REGISTER_CLASS()
class SoilMoisture(Sensor):
    """Soil Moisture sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.HUMIDITY
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PERCENTAGE


@REGISTER_CLASS()
class LeafWetness(Sensor):
    """Leaf Wetness sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.HUMIDITY
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PERCENTAGE


@REGISTER_CLASS()
class Illuminance(Sensor):
    """Illuminance Sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.ILLUMINANCE
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = LIGHT_LUX


@REGISTER_CLASS()
class SmartEnergyMetering(Sensor):
    """Metering sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.POWER
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT

    unit_of_measure_map = {
        0x00: POWER_WATT,
        0x01: VOLUME_FLOW_RATE_CUBIC_METERS_PER_HOUR,
        0x02: VOLUME_FLOW_RATE_CUBIC_FEET_PER_MINUTE,
        0x03: f"100 {VOLUME_FLOW_RATE_CUBIC_METERS_PER_HOUR}",
        0x04: f"US {VOLUME_GALLONS}/{TIME_HOURS}",
        0x05: f"IMP {VOLUME_GALLONS}/{TIME_HOURS}",
        0x06: f"BTU/{TIME_HOURS}",
        0x07: f"l/{TIME_HOURS}",
        0x08: "kPa",  # gauge
        0x09: "kPa",  # absolute
        0x0A: f"1000 {VOLUME_GALLONS}/{TIME_HOURS}",
        0x0B: "unitless",
        0x0C: f"MJ/{TIME_SECONDS}",
    }

    def __init__(self, *args, **kwargs):
        """Initialize the ZHA switch."""
        super().__init__(*args, **kwargs)
        self._state = self._platform_entity.state.state
        self._unit = self.unit_of_measure_map.get(self._platform_entity.unit)

        if hasattr(self._platform_entity.state, "device_type"):
            self._extra_state_attributes = {
                "device_type": self._platform_entity.state.device_type,
            }
            if hasattr(self._platform_entity.state, "status"):
                status = self._platform_entity.state.status
                if status is not None:
                    status = status.title().replace("_", " ")
                self._extra_state_attributes["status"] = status

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = event.state["state"]
        self._extra_state_attributes = {}
        if hasattr(self._platform_entity.state, "device_type"):
            self._extra_state_attributes = {
                "device_type": self._platform_entity.state.device_type,
            }
            if hasattr(self._platform_entity.state, "status"):
                status = self._platform_entity.state.status
                if status is not None:
                    status = status.title().replace("_", " ")
                self._extra_state_attributes["status"] = status
        self.async_write_ha_state()


@REGISTER_CLASS()
class SmartEnergySummation(SmartEnergyMetering):
    """Smart Energy Metering summation sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.ENERGY
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING


@REGISTER_CLASS()
class Pressure(Sensor):
    """Pressure sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.PRESSURE
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PRESSURE_HPA


@REGISTER_CLASS()
class Temperature(Sensor):
    """Temperature Sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.TEMPERATURE
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = TEMP_CELSIUS


@REGISTER_CLASS()
class CarbonDioxideConcentration(Sensor):
    """Carbon Dioxide Concentration sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.CO2
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_PARTS_PER_MILLION


@REGISTER_CLASS()
class CarbonMonoxideConcentration(Sensor):
    """Carbon Monoxide Concentration sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.CO
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_PARTS_PER_MILLION


@REGISTER_CLASS()
class VOCLevel(Sensor):
    """VOC Level sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_MICROGRAMS_PER_CUBIC_METER


@REGISTER_CLASS()
class PPBVOCLevel(Sensor):
    """VOC Level sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_PARTS_PER_BILLION


@REGISTER_CLASS()
class FormaldehydeConcentration(Sensor):
    """Formaldehyde Concentration sensor."""

    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_PARTS_PER_MILLION


@REGISTER_CLASS()
class ThermostatHVACAction(Sensor):
    """Thermostat HVAC action sensor."""


@REGISTER_CLASS()
class SinopeHVACAction(ThermostatHVACAction):
    """Sinope Thermostat HVAC action sensor."""


@REGISTER_CLASS()
class RSSISensor(Sensor):
    """RSSI sensor for a device."""

    _state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _device_class: SensorDeviceClass = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = ENTITY_CATEGORY_DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


@REGISTER_CLASS()
class LQISensor(RSSISensor):
    """LQI sensor for a device."""
