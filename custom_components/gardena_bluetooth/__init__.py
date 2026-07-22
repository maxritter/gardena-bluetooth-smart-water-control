"""The Gardena Bluetooth integration."""

import logging
import sys
from pathlib import Path

# Prefer the vendored, patched copy of gardena-bluetooth shipped under
# `_vendored/` over any version installed in the HA Python environment.
# The vendored copy adds:
#   * scan filter relaxation for devices that don't advertise the legacy
#     ScanService UUID (G-1903x family — Smart Water Control etc.);
#   * Valve1/Valve2 ValveX type annotations corrected to CharacteristicIntKeys;
#   * Client.start_watering / stop_watering helpers + the WATERING_COMMAND_SOURCE
#     constant ("18") required by the LWM2M Execute protocol on those devices.
# Once the upstream library exposes this support, the `_vendored/` directory
# and these few lines can be deleted.
_VENDORED = str(Path(__file__).parent / "_vendored")
if _VENDORED not in sys.path:
    sys.path.insert(0, _VENDORED)
for _mod in [m for m in list(sys.modules) if m == "gardena_bluetooth" or m.startswith("gardena_bluetooth.")]:
    del sys.modules[_mod]

from bleak.backends.device import BLEDevice
from gardena_bluetooth.client import CachedConnection, Client
from gardena_bluetooth.const import AquaContour, DeviceConfiguration, DeviceInformation
from gardena_bluetooth.exceptions import (
    CharacteristicNoAccess,
    CharacteristicNotFound,
    CommunicationFailure,
)
from gardena_bluetooth.parse import CharacteristicTime, ProductType
from gardena_bluetooth.scan import async_get_manufacturer_data

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import CONF_PRODUCT_TYPE, DOMAIN
from .coordinator import (
    DeviceUnavailable,
    GardenaBluetoothConfigEntry,
    GardenaBluetoothCoordinator,
)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.VALVE,
]
LOGGER = logging.getLogger(__name__)
TIMEOUT = 20.0
DISCONNECT_DELAY = 5
# One connect timeout must not fail a whole poll cycle; the garden device
# regularly misses the first connection attempt.
CONNECT_ATTEMPTS = 4


def get_connection(hass: HomeAssistant, address: str) -> CachedConnection:
    """Set up a cached client that keeps connection after last use."""

    def _device_lookup() -> BLEDevice:
        device = bluetooth.async_ble_device_from_address(
            hass, address, connectable=True
        )
        if not device:
            raise DeviceUnavailable("Unable to find device")
        return device

    return CachedConnection(DISCONNECT_DELAY, _device_lookup, max_attempts=CONNECT_ATTEMPTS)


async def _update_timestamp(client: Client, characteristics: CharacteristicTime):
    try:
        await client.update_timestamp(characteristics, dt_util.now())
    except CharacteristicNotFound:
        pass
    except CharacteristicNoAccess:
        LOGGER.debug("No access to update internal time")


async def async_setup_entry(
    hass: HomeAssistant, entry: GardenaBluetoothConfigEntry
) -> bool:
    """Set up Gardena Bluetooth from a config entry."""

    address = entry.data[CONF_ADDRESS]

    # Prefer the product type stored at pairing time: Aqua Contours devices
    # only advertise their product TLV while in pairing mode, so a live scan
    # can never succeed after a restart. Fall back to scanning only for
    # entries created before CONF_PRODUCT_TYPE existed, and migrate them.
    product_type = ProductType.UNKNOWN
    if stored := entry.data.get(CONF_PRODUCT_TYPE):
        try:
            product_type = ProductType[stored]
        except KeyError:
            LOGGER.warning("Ignoring unknown stored product type: %s", stored)

    if product_type == ProductType.UNKNOWN:
        try:
            mfg_data = await async_get_manufacturer_data({address})
        except TimeoutError as exception:
            # Device not advertising (asleep / out of range) - let HA retry
            # with backoff instead of failing the entry permanently.
            raise ConfigEntryNotReady(
                f"Device {address} not found during scan"
            ) from exception
        product_type = mfg_data[address].product_type
        if product_type == ProductType.UNKNOWN:
            raise ConfigEntryNotReady("Unable to find product type")
        # One-time migration: persist so the next restart needs no scan.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_PRODUCT_TYPE: product_type.name}
        )

    client = Client(get_connection(hass, address), product_type)
    try:
        chars = await client.get_all_characteristics()

        sw_version = await client.read_char(DeviceInformation.firmware_version, None)
        manufacturer = await client.read_char(DeviceInformation.manufacturer_name, None)
        model = await client.read_char(DeviceInformation.model_number, None)

        name = entry.title
        name = await client.read_char(DeviceConfiguration.custom_device_name, name)
        name = await client.read_char(AquaContour.custom_device_name, name)

        await _update_timestamp(client, DeviceConfiguration.unix_timestamp)
        await _update_timestamp(client, AquaContour.unix_timestamp)

    except (TimeoutError, CommunicationFailure, DeviceUnavailable) as exception:
        await client.disconnect()
        raise ConfigEntryNotReady(
            f"Unable to connect to device {address} due to {exception}"
        ) from exception

    device = DeviceInfo(
        identifiers={(DOMAIN, address)},
        connections={(dr.CONNECTION_BLUETOOTH, address)},
        name=name,
        sw_version=sw_version,
        manufacturer=manufacturer,
        model=model,
    )

    coordinator = GardenaBluetoothCoordinator(
        hass, entry, LOGGER, client, set(chars.keys()), device, address
    )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_refresh()

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GardenaBluetoothConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()

    return unload_ok
