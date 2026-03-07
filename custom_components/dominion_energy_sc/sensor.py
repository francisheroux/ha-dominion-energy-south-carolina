"""Sensor platform for Dominion Energy South Carolina."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AccountData, DominionEnergySCCoordinator

# Custom unit for CCF (no HA constant available prior to 2024.x)
_UNIT_CCF = "CCF"
_UNIT_USD = "USD"
_UNIT_USD_PER_KWH = "USD/kWh"
_UNIT_USD_PER_CCF = "USD/CCF"
_UNIT_KWH_PER_DAY = "kWh/day"
_UNIT_CCF_PER_DAY = "CCF/day"


@dataclass(frozen=True, kw_only=True)
class DominionSensorEntityDescription(SensorEntityDescription):
    """Extended description with a value getter and optional guard."""

    value_fn: Callable[[AccountData], Any]
    # If set, sensor is only created when this flag is True on AccountData
    requires_electric: bool = False
    requires_gas: bool = False


SENSOR_DESCRIPTIONS: tuple[DominionSensorEntityDescription, ...] = (
    DominionSensorEntityDescription(
        key="balance",
        name="Current Balance",
        native_unit_of_measurement=_UNIT_USD,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.balance,
    ),
    DominionSensorEntityDescription(
        key="due_date",
        name="Due Date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: d.due_date or None,
    ),
    DominionSensorEntityDescription(
        key="last_payment",
        name="Last Payment",
        native_unit_of_measurement=_UNIT_USD,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.last_payment,
    ),
    DominionSensorEntityDescription(
        key="avg_daily_electric_cost",
        name="Avg Daily Electric Cost",
        native_unit_of_measurement=_UNIT_USD,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        requires_electric=True,
        value_fn=lambda d: d.avg_daily_electric_cost,
    ),
    DominionSensorEntityDescription(
        key="avg_daily_electric_usage",
        name="Avg Daily Electric Usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        requires_electric=True,
        value_fn=lambda d: d.avg_daily_electric_usage,
    ),
    DominionSensorEntityDescription(
        key="avg_electric_rate",
        name="Avg Electric Rate",
        native_unit_of_measurement=_UNIT_USD_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        requires_electric=True,
        value_fn=lambda d: d.avg_per_unit_electric,
    ),
    DominionSensorEntityDescription(
        key="avg_daily_gas_cost",
        name="Avg Daily Gas Cost",
        native_unit_of_measurement=_UNIT_USD,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        requires_gas=True,
        value_fn=lambda d: d.avg_daily_gas_cost,
    ),
    DominionSensorEntityDescription(
        key="avg_daily_gas_usage",
        name="Avg Daily Gas Usage",
        native_unit_of_measurement=_UNIT_CCF_PER_DAY,
        state_class=SensorStateClass.MEASUREMENT,
        requires_gas=True,
        value_fn=lambda d: d.avg_daily_gas_usage,
    ),
    DominionSensorEntityDescription(
        key="avg_gas_rate",
        name="Avg Gas Rate",
        native_unit_of_measurement=_UNIT_USD_PER_CCF,
        state_class=SensorStateClass.MEASUREMENT,
        requires_gas=True,
        value_fn=lambda d: d.avg_per_unit_gas,
    ),
    DominionSensorEntityDescription(
        key="avg_local_temperature",
        name="Avg Local Temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.avg_temp,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator: DominionEnergySCCoordinator = entry.runtime_data

    entities: list[DominionEnergySCSensor] = []
    for account_number, account_data in coordinator.data.items():
        for description in SENSOR_DESCRIPTIONS:
            if description.requires_electric and not account_data.has_electric:
                continue
            if description.requires_gas and not account_data.has_gas:
                continue
            entities.append(
                DominionEnergySCSensor(coordinator, account_number, description)
            )

    async_add_entities(entities)


class DominionEnergySCSensor(
    CoordinatorEntity[DominionEnergySCCoordinator], SensorEntity
):
    """A sensor for a Dominion Energy SC account."""

    entity_description: DominionSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DominionEnergySCCoordinator,
        account_number: str,
        description: DominionSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._account_number = account_number
        self._attr_unique_id = f"{account_number}_{description.key}"

    @property
    def _account_data(self) -> AccountData | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._account_number)
        return None

    @property
    def device_info(self) -> DeviceInfo:
        data = self._account_data
        display = data.display_number if data else self._account_number[:8]
        address = data.address if data else ""
        return DeviceInfo(
            identifiers={(DOMAIN, self._account_number)},
            name=f"Dominion Energy SC {display}",
            manufacturer="Dominion Energy",
            model="South Carolina",
            configuration_url="https://account.dominionenergysc.com/",
            suggested_area=address,
        )

    @property
    def available(self) -> bool:
        data = self._account_data
        if data is None:
            return False
        if data.is_closed:
            return False
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> Any:
        data = self._account_data
        if data is None:
            return None
        return self.entity_description.value_fn(data)
