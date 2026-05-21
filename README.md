# Gardena Bluetooth — Smart Water Control patch (G-19033 / G-19034)

**Stopgap custom integration that overrides the built-in `gardena_bluetooth`
component in Home Assistant with support for the newer Smart Water Control
family of devices (G-19033-20 `wc_single`, G-19034-20 `wc_dual`, G-19050-20
pipeline, …).**

These devices have been on sale since 2025 and are the direct successors of
the now-EOL Bluetooth-only 01889-20. The current built-in integration does
not discover or control them at all (home-assistant/core#167291,
home-assistant/discussions/3056).

## What this fork fixes

1. **Discovery** — The new family advertises only manufacturer data, no
   service UUIDs. Adds a second BT matcher with `manufacturer_id: 1062`
   only and relaxes the scanner / config-flow filters.
2. **Valve actuation** — Implements the LWM2M-Execute protocol the new
   devices expect (`0='18',1='<duration>'`), reverse-engineered from
   cloudless-garden/gardena-smart-local-api and verified live.
3. **Battery** — Exposes the standard BLE Battery Service (`0x180f`) for
   the Valve1/Valve2 family.
4. **Entities** — Adds `GardenaBluetoothValveX` (open/close), switch
   alias, manual-watering-duration number, remaining-time sensor,
   activation-reason sensor, valve-available binary sensor.

## Status

* Verified live on a G-19033-20 (firmware 1.1.1).
* The upstream fixes are in flight:
  * Library — https://github.com/elupus/gardena-bluetooth/pull/49
  * HA component — https://github.com/home-assistant/core/pull/171759

Delete this custom integration once the official component picks up the
changes.

## Install via HACS

This repo is already structured for HACS: **HACS → Integrations → ⋮ →
Custom repositories → add `maxritter/gardena-bluetooth-smart-water-control`
(category: Integration) → Install → Restart Home Assistant.**

Then **Settings → Devices & Services → Add Integration → Gardena Bluetooth**,
or wait for auto-discovery once the device is in BLE range.

## Pairing checklist

1. Power-cycle / factory-reset the device (hold Man. button while
   inserting the battery for ~10s — all 3 LEDs flash).
2. Ensure HA can reach the device via BLE (host adapter or an ESPHome
   Bluetooth proxy in range).
3. Watch Settings → Devices & Services — `G-19033` (or `G-19034`) should
   appear within ~30s.
