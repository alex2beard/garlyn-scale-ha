# GARLYN Scale for Home Assistant

Unofficial Home Assistant custom integration for GARLYN Bodyscan Master scales.

The intended data path is:

```text
GARLYN scale -> BLE -> ESP32/ESPHome -> local webhook -> Home Assistant
```

The ESP32 handles BLE framing and reliable delivery. Home Assistant owns user
profiles, body-composition calculations, entities/history, and optional
SparkyFitness synchronization.

> This is an unofficial community project based on reverse engineering. It is
> not affiliated with GARLYN, MovingLife, or Senssun.

## Current status

The repository now contains an executable profile-aware measurement pipeline:

- a config flow that creates a scale entry, stable scale ID, and random webhook;
- an automatically reloading options flow for adding, editing, and deleting
  profiles with unique four-digit PINs;
- a local-only, POST-only webhook registration;
- a strict versioned transport parser;
- bounded, restart-safe duplicate detection backed by Home Assistant storage;
- exact PIN-to-profile resolution and age calculation on the measurement date;
- atomic storage of the accepted measurement, resolved profile snapshot, and
  calculated result;
- an isolated Python port of the eight verified `bodyFatScaleAlg_2.2.2`
  outputs;
- one physical Home Assistant device per scale and nine push sensors per
  configured profile;
- stable profile/entity identity across profile renames and PIN changes, with
  automatic entity cleanup when a profile is deleted;
- a privacy-safe synthetic regression fixture and unit tests;
- an ESPHome external component that reassembles and validates fragmented FFF3
  measurements, decodes all ten BIA values, persists a bounded delivery queue,
  and posts transport-v1 JSON to the Home Assistant webhook;
- optional per-profile SparkyFitness synchronization of the seven agreed body
  measurements through a restart-safe Home Assistant outbox.

The direct ESPHome-to-Home Assistant path has been field-tested with a physical
scale, including webhook retry and delivery-queue recovery after an ESP restart.
Calculated fields beyond the eight verified outputs remain disabled until their
semantics are independently confirmed.

No `DataUpdateCoordinator` is used: this is a push integration.

## Installation

### HACS custom repository

This integration is distributed through GitHub Releases as a HACS custom
repository. It is not currently included in the HACS default repository list:

1. Open HACS, select **Integrations**, then open the three-dot menu.
2. Select **Custom repositories**.
3. Add `https://github.com/alex2beard/garlyn-scale-ha` with category
   **Integration**.
4. Download **GARLYN Scale**, restart Home Assistant, then add the integration
   from **Settings -> Devices & services**.

After setup, open **Configure -> Connection information** on the integration
entry to view the persistent scale ID and local webhook path used by the
measurement transport.

HACS cannot add a private GitHub repository. The integration is also installable
manually by copying `custom_components/garlyn_scale` into the matching directory
under the Home Assistant configuration folder and restarting Home Assistant.

### Release policy

Stable, field-tested versions are published as GitHub Releases named
`vX.Y.Z`. HACS users should install the latest release. The `main` branch is the
development channel and may contain changes that have not completed field
testing.

ESPHome configurations should pin the external component to a tested release
tag. An integration-only update does not require reflashing the ESP; update its
`ref` only when the release notes describe an ESPHome component change.

## Confirmed measurement model

A completed eight-electrode measurement contains:

- a four-digit GARLYN profile PIN;
- weight;
- five segmental impedance values at 20 kHz;
- five segmental impedance values at 100 kHz.

The fixed segment order is:

1. left arm;
2. right arm;
3. left leg;
4. right leg;
5. trunk.

Raw impedance is an internal calculation input, not a normal Home Assistant
sensor.

## Home Assistant entities

Each config entry creates one physical `GARLYN Bodyscan Master` device. Every
configured profile contributes these nine sensors to that device:

| Sensor | Unit | Source |
| --- | --- | --- |
| Weight | kg | Completed measurement |
| Body fat | % | Verified calculation |
| Body fat mass | kg | Verified calculation |
| Muscle | % | Verified calculation |
| Muscle mass | kg | Verified calculation |
| Body water | % | Verified calculation |
| Body water mass | kg | Verified calculation |
| Basal metabolic rate | kcal/day | Verified calculation |
| Body mass index | none | Verified calculation |

The entities are push-based and update only for their own profile after the
accepted sample has been persisted. A new measurement is recorded even when its
numeric result matches the preceding measurement; a retry with the same
`measurement_id` is not recorded twice. The latest persisted values are
available immediately after a Home Assistant restart.

Entity unique IDs contain a private stable profile ID, not the mutable name or
PIN. Existing legacy profiles receive a deterministic ID during loading. A PIN
change therefore keeps the same entities and latest readings, while deleting a
profile removes its entities from the entity registry.

## Profile schema

The current options flow stores:

- name;
- GARLYN profile PIN;
- sex;
- date of birth (age is calculated for the measurement date);
- height in centimetres;
- Athlete Mode;
- reference standard;
- optional SparkyFitness enable switch and private API key.

It also maintains an internal stable profile ID used for entity continuity. The
ID is not exposed as a user-editable setting.

The SparkyFitness base URL is configured once per physical scale. Each profile
has its own enable switch and API key, so the key selects the matching Sparky
account. Optional Home Assistant person linkage remains a future extension.

## SparkyFitness synchronization

Synchronization sends these seven values after Home Assistant has accepted and
persisted a measurement:

| GARLYN value | Sparky type | Storage | Unit |
| --- | --- | --- | --- |
| Weight | `weight` | Native | kg |
| Body fat | `body_fat_percentage` | Native | % |
| Body fat mass | `body_fat_mass_kg` | Custom category | kg |
| Muscle | `muscle_percentage` | Custom category | % |
| Muscle mass | `muscle_mass_kg` | Native | kg |
| Body water | `body_water_percentage` | Native | % |
| Body water mass | `body_water_mass_kg` | Custom category | kg |

BMI, BMR, bone mass, profile data, and raw BIA values are not sent. Custom
categories appear automatically after the first successful delivery and can be
given friendly display names in Sparky without changing their technical names.
The three custom records use Sparky's `manual` source so their synchronized
values are visible in the current Sparky Mobile daily form; the four native
records retain the descriptive `GARLYN Scale via Home Assistant` source.

Configure the scale-wide base URL under **Configure -> SparkyFitness settings**,
then edit each profile that should synchronize and enter its own API key. The
key is stored only in Home Assistant and is never sent to the ESP. HTTPS is
recommended.

Sparky delivery runs in the background and cannot delay the ESP webhook. Pending
work survives Home Assistant restarts, retries with exponential backoff, and
keeps only the newest measurement for a profile and local date. A Sparky outage
therefore does not interrupt Home Assistant entities or history.

See [SparkyFitness synchronization](docs/sparkyfitness.md) for setup, exact
delivery semantics, response validation, privacy boundaries, and troubleshooting.

### Athlete Mode and native activity values

MovingLife 5.12.9 currently exposes a single **Athlete Mode** switch:

| UI value | Native `bhActivityLevel` |
| --- | ---: |
| Off | 0 |
| On | 5 |

The native library still accepts the complete legacy range `0..5`:

| Value | Meaning | AMR factor |
| ---: | --- | ---: |
| 0 | Standard mode | 1.54 male / 1.32 female |
| 1 | Sedentary | 1.20 |
| 2 | Light activity | 1.375 |
| 3 | Moderate activity | 1.55 |
| 4 | Very active | 1.725 |
| 5 | Extremely active / Athlete Mode | 1.90 |

Activity affects the AMR calculation only. It does not change the verified raw
BMI, BMR, body-fat, muscle, or water values. The UI therefore models the current
MovingLife switch, while the pure domain model preserves all native values.

### Reference standard

The native field is named `bhNationality`, but it is a binary reference-standard
selector, not an arbitrary nationality string:

| Value | Adult BMI category thresholds |
| --- | --- |
| `external` | 25 / 30 |
| `internal` | 24 / 28 (China) |

It changes classification/reference ranges, body type, and body score. It does
not change the raw BMI number or the verified body-fat, muscle, water, and BMR
numbers. The integration calls this option **Reference standard** and defaults to
`external`.

Public documentation does not record any person's current Athlete Mode or
reference-standard setting. All public examples and fixtures use synthetic
profile values.

## ESP -> Home Assistant transport v1

The ESP sends one JSON POST per completed measurement:

```json
{
  "protocol_version": 1,
  "scale_id": "synthetic_scale_1",
  "measurement_id": "synthetic-reference-001",
  "measured_at": "2026-01-15T12:00:00+00:00",
  "profile_pin": "4242",
  "weight_kg": 74.8,
  "bia": {
    "20khz": [410.2, 408.6, 360.4, 355.9, 30.1],
    "100khz": [365.1, 363.8, 315.6, 312.2, 26.5]
  }
}
```

The parser rejects unknown fields, wrong protocol versions, scale-ID mismatch,
timestamps without a UTC offset, malformed PINs, non-finite numbers, and BIA
arrays that do not contain exactly five positive values.

After transport validation, Home Assistant resolves `profile_pin`, derives the
profile age at `measured_at`, runs the verified calculation, and only then commits
the sample to runtime state. A successful first delivery returns `202 accepted`;
an idempotent retry returns `200 duplicate`. An unknown or temporally invalid
profile returns `409` and the measurement ID is deliberately not added to the
deduplication cache, so the ESP can retry after the profile configuration is
fixed.

Delivery semantics are designed as:

```text
ESP delivery:  at least once
HA processing: once per measurement_id
```

The bounded cache is persisted per config entry with Home Assistant's versioned
`Store` helper. It retains insertion/LRU order and the latest immutable
calculated snapshot for every profile. The storage file is private and written
atomically. State is restored before entities and the webhook are set up, so
sensor values are present immediately and a retry of a previously accepted
`measurement_id` remains a duplicate after Home Assistant restarts. Rejected
measurements are never added to the cache or storage.

## ESPHome BLE decoder

The repository includes the `garlyn_scale_ble` ESPHome external component. It
owns the `FFF3` indication subscription, reassembles the usual fragmented
68-byte result, verifies its checksum and BCD profile PIN, decodes weight and
all ten impedances, and emits the exact JSON shape documented above to the ESP
log at verbose level. It stores up to eight pending payloads in ESP32 NVS before
attempting delivery and removes one only after Home Assistant returns `202`
(`accepted`) or `200` (`duplicate`). Raw frames are not logged.

See
[`docs/esphome-transport.md`](docs/esphome-transport.md) for the confirmed frame
layout, privacy-safe test vector, queue behavior, and ESPHome configuration.

## Synthetic public regression vector

`tests/fixtures/synthetic_reference.json` contains a deliberately synthetic
profile, measurement, and expected result:

- weight: 74.8 kg;
- body fat: 32.804783% / 24.53798 kg;
- muscle: 62.367653% / 46.65101 kg;
- body water: 49.125523% / 36.745892 kg;
- BMR: 1455;
- BMI: 24.42449.

The vector locks the public Python implementation's binary32 behavior but is not
a real person's measurement and must not be treated as physiological reference
data. Native equivalence was checked during development with private captured
data; those personal inputs and outputs are intentionally excluded from the
public repository.

## Development

Run the pure unit tests from the repository root:

```bash
python -m pytest
```

## License

This project is available under the [MIT License](LICENSE).
