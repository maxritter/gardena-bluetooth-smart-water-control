"""Provides the DataUpdateCoordinator."""

import asyncio
from datetime import timedelta
import logging

from gardena_bluetooth.client import Client
from gardena_bluetooth.exceptions import (
    CharacteristicNoAccess,
    GardenaBluetoothException,
)
from gardena_bluetooth.parse import Characteristic, CharacteristicType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

# 20s keeps manual actions on the device reasonably fresh in HA;
# the device carries no valve state in its advertisements, so
# polling is the only way to observe external changes.
SCAN_INTERVAL = timedelta(seconds=20)
# Consecutive failed polls tolerated (serving cached data) before entities
# are marked unavailable. 3 polls = ~60s bridge over transient proxy wedges.
MAX_FAILED_POLLS = 3
LOGGER = logging.getLogger(__name__)

type GardenaBluetoothConfigEntry = ConfigEntry[GardenaBluetoothCoordinator]


class DeviceUnavailable(HomeAssistantError):
    """Raised if device can't be found."""


class GardenaBluetoothCoordinator(DataUpdateCoordinator[dict[str, bytes]]):
    """Class to manage fetching data."""

    config_entry: GardenaBluetoothConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GardenaBluetoothConfigEntry,
        logger: logging.Logger,
        client: Client,
        characteristics: set[str],
        device_info: DeviceInfo,
        address: str,
    ) -> None:
        """Initialize global data updater."""
        super().__init__(
            hass=hass,
            logger=logger,
            config_entry=config_entry,
            name="Gardena Bluetooth Data Update Coordinator",
            update_interval=SCAN_INTERVAL,
        )
        self.address = address
        self.data = {}
        self._failed_polls = 0
        self.client = client
        self.characteristics = characteristics
        self.device_info = device_info

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and any connection.

        The disconnect is bounded: a wedged proxy connection (phantom BLE
        connection on an ESPHome proxy) used to make this await forever,
        leaving the config entry stuck in unload_in_progress until a full
        Home Assistant restart. Dropping the connection after a timeout is
        always safe - the proxy cleans up on its next reconnect.
        """
        await super().async_shutdown()
        try:
            async with asyncio.timeout(10):
                await self.client.disconnect()
        except (TimeoutError, GardenaBluetoothException) as exception:
            LOGGER.warning(
                "Disconnect of %s timed out or failed during shutdown; "
                "dropping connection (%s)",
                self.address,
                exception,
            )

    async def _async_update_data(self) -> dict[str, bytes]:
        """Poll the device."""
        uuids: set[str] = {
            uuid for context in self.async_contexts() for uuid in context
        }
        if not uuids:
            return {}

        data: dict[str, bytes] = {}
        for uuid in uuids:
            try:
                data[uuid] = await self.client.read_char_raw(uuid)
            except CharacteristicNoAccess as exception:
                LOGGER.debug("Unable to get data for %s due to %s", uuid, exception)
            except (GardenaBluetoothException, DeviceUnavailable) as exception:
                # Grace period: BLE proxies occasionally wedge for a poll or
                # two (dropped-connection bug in ESPHome 2025.x); blanking
                # every entity on the first failed 20s poll made the device
                # flap between available and unavailable. Serve last-known
                # data for up to MAX_FAILED_POLLS consecutive failures, then
                # report unavailable for real.
                self._failed_polls += 1
                if self.data and self._failed_polls <= MAX_FAILED_POLLS:
                    LOGGER.warning(
                        "Poll %d/%d for %s failed (%s); serving cached data",
                        self._failed_polls,
                        MAX_FAILED_POLLS,
                        self.address,
                        exception,
                    )
                    return self.data
                raise UpdateFailed(
                    f"Unable to update data for {uuid} due to {exception}"
                ) from exception
        self._failed_polls = 0
        return data

    def get_cached(
        self, char: Characteristic[CharacteristicType]
    ) -> CharacteristicType | None:
        """Read cached characteristic."""
        if data := self.data.get(char.uuid):
            return char.decode(data)
        return None

    async def write(
        self, char: Characteristic[CharacteristicType], value: CharacteristicType
    ) -> None:
        """Write characteristic to device."""
        try:
            await self.client.write_char(char, value)
        except (GardenaBluetoothException, DeviceUnavailable) as exception:
            raise HomeAssistantError(
                f"Unable to write characteristic {char} dur to {exception}"
            ) from exception

        self.data[char.uuid] = char.encode(value)
        await self.async_refresh()

    async def read_char_until(
        self,
        char: Characteristic[CharacteristicType],
        expected: CharacteristicType,
        attempts: int = 10,
        interval: float = 1.0,
    ) -> CharacteristicType | None:
        """Poll a characteristic until it reports the expected value.

        Used for commands the device applies asynchronously, where an
        immediate readback reports stale data. Returns the last read value,
        which is also cached for listeners.
        """
        value: CharacteristicType | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(interval)
            try:
                value = await self.client.read_char(char)
            except (GardenaBluetoothException, DeviceUnavailable):
                break
            if value == expected:
                break
        if value is not None:
            self.data[char.uuid] = char.encode(value)
        return value
