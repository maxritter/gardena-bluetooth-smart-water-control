"""Support for switch entities."""

from typing import Any

from gardena_bluetooth.const import Valve, Valve1, Valve2, ValveX

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GardenaBluetoothConfigEntry, GardenaBluetoothCoordinator
from .entity import GardenaBluetoothEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GardenaBluetoothConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities based on a config entry."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = []

    if GardenaBluetoothValveSwitch.characteristics.issubset(
        coordinator.characteristics
    ):
        entities.append(GardenaBluetoothValveSwitch(coordinator))

    for service in (Valve1, Valve2):
        required = {
            service.state.unique_id,
            service.start_watering.unique_id,
            service.stop_watering.unique_id,
        }
        if required.issubset(coordinator.characteristics):
            entities.append(GardenaBluetoothValveXSwitch(coordinator, service))

    async_add_entities(entities)


class GardenaBluetoothValveSwitch(GardenaBluetoothEntity, SwitchEntity):
    """Switch alias for the old single-valve Bluetooth-only Water Control."""

    characteristics = {
        Valve.state.unique_id,
        Valve.manual_watering_time.unique_id,
        Valve.remaining_open_time.unique_id,
    }

    def __init__(
        self,
        coordinator: GardenaBluetoothCoordinator,
    ) -> None:
        """Initialize the switch."""
        super().__init__(
            coordinator, {Valve.state.uuid, Valve.manual_watering_time.uuid}
        )
        self._attr_unique_id = f"{coordinator.address}-{Valve.state.unique_id}"
        self._attr_translation_key = "state"
        self._attr_is_on = None
        self._attr_entity_registry_enabled_default = False

    def _handle_coordinator_update(self) -> None:
        self._attr_is_on = self.coordinator.get_cached(Valve.state)
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        if not (data := self.coordinator.data.get(Valve.manual_watering_time.uuid)):
            raise HomeAssistantError("Unable to get manual activation time.")

        value = Valve.manual_watering_time.decode(data)
        await self.coordinator.write(Valve.remaining_open_time, value)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self.coordinator.write(Valve.remaining_open_time, 0)
        self._attr_is_on = False
        self.async_write_ha_state()


class GardenaBluetoothValveXSwitch(GardenaBluetoothEntity, SwitchEntity):
    """Switch alias for the Smart Water Control family (Valve1/Valve2)."""

    _converging = False

    def __init__(
        self,
        coordinator: GardenaBluetoothCoordinator,
        service: type[ValveX],
    ) -> None:
        """Initialize the switch."""
        super().__init__(
            coordinator,
            {
                service.state.uuid,
                service.manual_watering_duration.uuid,
                service.available.uuid,
            },
        )
        self._service = service
        self._attr_unique_id = f"{coordinator.address}-{service.state.unique_id}"
        self._attr_translation_key = (
            "state_valve_2" if service is Valve2 else "state_valve_1"
        )
        self._attr_is_on = None
        self._attr_entity_registry_enabled_default = False

    def _handle_coordinator_update(self) -> None:
        # While a command is converging, readbacks may predate the command
        # being applied by the device; the command handler sets the state.
        if not self._converging:
            self._attr_is_on = self.coordinator.get_cached(self._service.state)
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on for the configured manual watering duration."""
        duration = (
            self.coordinator.get_cached(self._service.manual_watering_duration) or 1800
        )
        self._converging = True
        try:
            await self.coordinator.client.start_watering(self._service, duration)
            await self._async_wait_for_state(True)
        finally:
            self._converging = False

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        self._converging = True
        try:
            await self.coordinator.client.stop_watering(self._service)
            await self._async_wait_for_state(False)
        finally:
            self._converging = False

    async def _async_wait_for_state(self, target: bool) -> None:
        """Wait for the asynchronously applied watering command to be reflected."""
        state = await self.coordinator.read_char_until(self._service.state, target)
        self._attr_is_on = target if state is None else state
        self.async_write_ha_state()
