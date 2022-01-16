"""Sensors on Zigbee Home Automation networks."""
from __future__ import annotations

import logging

from zhaws.client.model.events import PlatformEntityEvent

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.zhaws.const import ZHAWS
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
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .entity import ZhaEntity

BATTERY_SIZES = {
    0: "No battery",
    1: "Built in",
    2: "Other",
    3: "AA",
    4: "AAA",
    5: "C",
    6: "D",
    7: "CR2",
    8: "CR123A",
    9: "CR2450",
    10: "CR2032",
    11: "CR1632",
    255: "Unknown",
}

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Flo sensors from config entry."""
    entities: list[Sensor] = []
    devices = hass.data[ZHAWS][config_entry.entry_id].devices
    for device in devices.values():
        for entity in device.device.entities.values():
            _LOGGER.info("processed entity: %s", entity)
            if entity.platform != Platform.SENSOR:
                continue
            if entity.class_name == "AnalogInput":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(AnalogInput(device, entity))
            elif entity.class_name == "Battery":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Battery(device, entity))
            elif entity.class_name == "Humidity":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Humidity(device, entity))
            elif entity.class_name == "Illuminance":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Illuminance(device, entity))
            elif entity.class_name == "ElectricalMeasurement":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(ElectricalMeasurement(device, entity))
            elif entity.class_name == "ElectricalMeasurementApparentPower":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(ElectricalMeasurementApparentPower(device, entity))
            elif entity.class_name == "ElectricalMeasurementRMSCurrent":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(ElectricalMeasurementRMSCurrent(device, entity))
            elif entity.class_name == "ElectricalMeasurementRMSVoltage":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(ElectricalMeasurementRMSVoltage(device, entity))
            elif entity.class_name == "SoilMoisture":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(SoilMoisture(device, entity))
            elif entity.class_name == "Temperature":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Temperature(device, entity))
            elif entity.class_name == "LeafWetness":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(LeafWetness(device, entity))
            elif entity.class_name == "SmartEnergyMetering":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(SmartEnergyMetering(device, entity))
            elif entity.class_name == "SmartEnergySummation":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(SmartEnergySummation(device, entity))
            elif entity.class_name == "Pressure":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(Pressure(device, entity))
            elif entity.class_name == "CarbonDioxideConcentration":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(CarbonDioxideConcentration(device, entity))
            elif entity.class_name == "CarbonMonoxideConcentration":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(CarbonMonoxideConcentration(device, entity))
            elif entity.class_name == "VOCLevel":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(VOCLevel(device, entity))
            elif entity.class_name == "PPBVOCLevel":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(PPBVOCLevel(device, entity))
            elif entity.class_name == "FormaldehydeConcentration":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(FormaldehydeConcentration(device, entity))
            elif entity.class_name == "ThermostatHVACAction":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(ThermostatHVACAction(device, entity))
            elif entity.class_name == "SinopeHVACAction":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(SinopeHVACAction(device, entity))
            elif entity.class_name == "RSSISensor":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(RSSISensor(device, entity))
            elif entity.class_name == "LQISensor":
                _LOGGER.warning("adding entity: %s", entity)
                entities.append(LQISensor(device, entity))

    async_add_entities(entities)


class Sensor(ZhaEntity, SensorEntity):
    """Base ZHA sensor."""

    _unit: str | None = None

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = event.state
        self.async_write_ha_state()

    @callback
    def async_restore_last_state(self, last_state):
        """Restore previous state."""
        super().async_restore_last_state(last_state)
        self._state = last_state.state

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement of this entity."""
        return self._unit

    @property
    def native_value(self) -> StateType:
        """Return the state of the entity."""
        return self._state


class AnalogInput(Sensor):
    """Sensor that displays analog input values."""


class Battery(Sensor):
    """Battery sensor of power configuration cluster."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.BATTERY
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @callback
    def platform_entity_state_changed(self, event: PlatformEntityEvent) -> None:
        """Set the entity state."""
        _LOGGER.warning("Handling platform entity state changed: %s", event)
        self._state = event.state["state"]
        self._extra_state_attributes = {
            "battery_size": event.state["battery_size"],
            "battery_quantity": event.state["battery_quantity"],
            "battery_voltage": event.state["battery_voltage"],
        }
        self.async_write_ha_state()


class ElectricalMeasurement(Sensor):
    """Active power measurement."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.POWER
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = POWER_WATT

    """
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        #Return device state attrs for sensor.
        attrs = {}
        if self._channel.measurement_type is not None:
            attrs["measurement_type"] = self._channel.measurement_type

        max_attr_name = f"{self.SENSOR_ATTR}_max"
        if (max_v := self._channel.cluster.get(max_attr_name)) is not None:
            attrs[max_attr_name] = str(self.formatter(max_v))

        return attrs
    """


class ElectricalMeasurementApparentPower(ElectricalMeasurement):
    """Apparent power measurement."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.APPARENT_POWER
    _unit = POWER_VOLT_AMPERE


class ElectricalMeasurementRMSCurrent(ElectricalMeasurement):
    """RMS current measurement."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.CURRENT
    _unit = ELECTRIC_CURRENT_AMPERE


class ElectricalMeasurementRMSVoltage(ElectricalMeasurement):
    """RMS Voltage measurement."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.CURRENT
    _unit = ELECTRIC_POTENTIAL_VOLT


class Humidity(Sensor):
    """Humidity sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.HUMIDITY
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PERCENTAGE


class SoilMoisture(Sensor):
    """Soil Moisture sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.HUMIDITY
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PERCENTAGE


class LeafWetness(Sensor):
    """Leaf Wetness sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.HUMIDITY
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PERCENTAGE


class Illuminance(Sensor):
    """Illuminance Sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.ILLUMINANCE
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = LIGHT_LUX


class SmartEnergyMetering(Sensor):
    """Metering sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.POWER
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT

    """
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        #Return device state attrs for battery sensors.
        attrs = {}
        if self._channel.device_type is not None:
            attrs["device_type"] = self._channel.device_type
        if (status := self._channel.status) is not None:
            attrs["status"] = str(status)[len(status.__class__.__name__) + 1 :]
        return attrs
    """


class SmartEnergySummation(SmartEnergyMetering):
    """Smart Energy Metering summation sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.ENERGY
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING


class Pressure(Sensor):
    """Pressure sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.PRESSURE
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = PRESSURE_HPA


class Temperature(Sensor):
    """Temperature Sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.TEMPERATURE
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = TEMP_CELSIUS


class CarbonDioxideConcentration(Sensor):
    """Carbon Dioxide Concentration sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.CO2
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_PARTS_PER_MILLION


class CarbonMonoxideConcentration(Sensor):
    """Carbon Monoxide Concentration sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.CO
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_PARTS_PER_MILLION


class VOCLevel(Sensor):
    """VOC Level sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_MICROGRAMS_PER_CUBIC_METER


class PPBVOCLevel(Sensor):
    """VOC Level sensor."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_PARTS_PER_BILLION


class FormaldehydeConcentration(Sensor):
    """Formaldehyde Concentration sensor."""

    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _unit = CONCENTRATION_PARTS_PER_MILLION


class ThermostatHVACAction(Sensor):
    """Thermostat HVAC action sensor."""


class SinopeHVACAction(ThermostatHVACAction):
    """Sinope Thermostat HVAC action sensor."""


class RSSISensor(Sensor):
    """RSSI sensor for a device."""

    _state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _device_class: SensorDeviceClass = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = ENTITY_CATEGORY_DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


class LQISensor(RSSISensor):
    """LQI sensor for a device."""
