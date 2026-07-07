"""Support for valve entities."""

from typing import Any

from gardena_bluetooth.const import Valve, Valve1, Valve2, ValveX

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GardenaBluetoothConfigEntry, GardenaBluetoothCoordinator
from .entity import GardenaBluetoothEntity

FALLBACK_WATERING_TIME_IN_SECONDS = 60 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GardenaBluetoothConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up valve entities based on a config entry."""
    coordinator = entry.runtime_data
    entities: list[ValveEntity] = []

    if GardenaBluetoothValve.characteristics.issubset(coordinator.characteristics):
        entities.append(GardenaBluetoothValve(coordinator))

    for service in (Valve1, Valve2):
        required = {
            service.state.unique_id,
            service.start_watering.unique_id,
            service.stop_watering.unique_id,
        }
        if required.issubset(coordinator.characteristics):
            entities.append(GardenaBluetoothValveX(coordinator, service))

    async_add_entities(entities)


class GardenaBluetoothValve(GardenaBluetoothEntity, ValveEntity):
    """Old single-valve Bluetooth-only Water Control (e.g. 01889-20)."""

    _attr_name = None
    _attr_is_closed: bool | None = None
    _attr_reports_position = False
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_device_class = ValveDeviceClass.WATER

    characteristics = {
        Valve.state.unique_id,
        Valve.manual_watering_time.unique_id,
        Valve.remaining_open_time.unique_id,
    }

    def __init__(
        self,
        coordinator: GardenaBluetoothCoordinator,
    ) -> None:
        """Initialize the valve."""
        super().__init__(
            coordinator, {Valve.state.uuid, Valve.manual_watering_time.uuid}
        )
        self._attr_unique_id = f"{coordinator.address}-{Valve.state.unique_id}"

    def _handle_coordinator_update(self) -> None:
        self._attr_is_closed = not self.coordinator.get_cached(Valve.state)
        super()._handle_coordinator_update()

    async def async_open_valve(self, **kwargs: Any) -> None:
        """Open the valve for the configured manual watering time."""
        value = (
            self.coordinator.get_cached(Valve.manual_watering_time)
            or FALLBACK_WATERING_TIME_IN_SECONDS
        )
        await self.coordinator.write(Valve.remaining_open_time, value)
        self._attr_is_closed = False
        self.async_write_ha_state()

    async def async_close_valve(self, **kwargs: Any) -> None:
        """Close the valve."""
        await self.coordinator.write(Valve.remaining_open_time, 0)
        self._attr_is_closed = True
        self.async_write_ha_state()


class GardenaBluetoothValveX(GardenaBluetoothEntity, ValveEntity):
    """Smart Water Control family (G-19033, G-19034, etc.).

    These devices use the Valve1/Valve2 GATT services and the LWM2M
    Execute protocol on start_watering/stop_watering. Actuation is
    delegated to the Client.start_watering / stop_watering helpers in
    the gardena_bluetooth library.
    """

    _attr_is_closed: bool | None = None
    _attr_reports_position = False
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_device_class = ValveDeviceClass.WATER

    def __init__(
        self,
        coordinator: GardenaBluetoothCoordinator,
        service: type[ValveX],
    ) -> None:
        """Initialize the valve."""
        super().__init__(
            coordinator,
            {
                service.state.uuid,
                service.manual_watering_duration.uuid,
                service.remaining_time_open.uuid,
                service.available.uuid,
            },
        )
        self._service = service
        # For single-valve devices keep name=None (uses device name);
        # for dual-valve devices distinguish valve_1 vs valve_2.
        if service is Valve2:
            self._attr_translation_key = "valve_2"
        elif service is Valve1:
            self._attr_translation_key = "valve_1"
        self._attr_unique_id = f"{coordinator.address}-{service.state.unique_id}"

    def _handle_coordinator_update(self) -> None:
        state = self.coordinator.get_cached(self._service.state)
        self._attr_is_closed = None if state is None else not state
        # Transitions end only once the device reports the target state, so
        # a readback from before the command was applied cannot end them.
        if state:
            self._attr_is_opening = False
        elif state is not None:
            self._attr_is_closing = False
        super()._handle_coordinator_update()

    async def async_open_valve(self, **kwargs: Any) -> None:
        """Open the valve for the configured manual watering duration."""
        duration = (
            self.coordinator.get_cached(self._service.manual_watering_duration)
            or FALLBACK_WATERING_TIME_IN_SECONDS
        )
        self._attr_is_opening = True
        self.async_write_ha_state()
        try:
            await self.coordinator.client.start_watering(self._service, duration)
            await self._async_wait_for_state(True)
        finally:
            self._attr_is_opening = False
            self.async_write_ha_state()

    async def async_close_valve(self, **kwargs: Any) -> None:
        """Close the valve."""
        self._attr_is_closing = True
        self.async_write_ha_state()
        try:
            await self.coordinator.client.stop_watering(self._service)
            await self._async_wait_for_state(False)
        finally:
            self._attr_is_closing = False
            self.async_write_ha_state()

    async def _async_wait_for_state(self, target: bool) -> None:
        """Wait for the asynchronously applied watering command to be reflected."""
        state = await self.coordinator.read_char_until(self._service.state, target)
        if state is not None:
            self._attr_is_closed = not state
