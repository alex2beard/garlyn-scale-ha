# SparkyFitness synchronization

GARLYN Scale can optionally copy seven calculated body measurements from Home
Assistant to a self-hosted SparkyFitness account. Home Assistant remains the
system that accepts the ESP measurement and owns its entity history; Sparky is
an independent downstream destination.

## Prerequisites and setup

1. In SparkyFitness, create an API key for the user who owns the corresponding
   GARLYN profile. On deployments that expose fine-grained API permissions, the
   key must be allowed to write health data (`health_data_write`).
2. In Home Assistant, open the GARLYN Scale integration and select
   **Configure -> SparkyFitness settings**.
3. Enter only the Sparky base URL, for example
   `https://sparky.example.com`. Do not append `/api/health-data`.
4. Return to profile management, edit the profile, enable
   **Synchronize with SparkyFitness**, and paste that user's API key without the
   `Bearer` prefix.
5. Repeat step 4 for any other profile, using a separate key for its Sparky
   account.

HTTPS is strongly recommended because the API key is an authorization secret.
Disabling synchronization or deleting a profile removes that profile's pending
Sparky work when the integration reloads.

## Exact field mapping

The integration posts one JSON array to `POST /api/health-data`:

| Value | `type` | Sparky destination | `unit` |
| --- | --- | --- | --- |
| Weight | `weight` | Native daily check-in | `kg` |
| Body-fat percentage | `body_fat_percentage` | Native daily check-in | `%` |
| Body-fat mass | `body_fat_mass_kg` | Custom category | `kg` |
| Muscle percentage | `muscle_percentage` | Custom category | `%` |
| Muscle mass | `muscle_mass_kg` | Native daily check-in | `kg` |
| Body-water percentage | `body_water_percentage` | Native daily check-in | `%` |
| Body-water mass | `body_water_mass_kg` | Custom category | `kg` |

The three custom categories are created by Sparky on first successful receipt.
Their initial labels are their technical type names. In Sparky settings, change
only a category's display name if a friendlier label is wanted; keep its
technical name, numeric data type, daily frequency, and unit unchanged.

Every record has the same:

- `date`, derived from the physical measurement instant in the Home Assistant
  time zone;
- UTC `timestamp` of the physical measurement;
- `record_timezone`, containing the Home Assistant IANA time-zone name.

The four native records use `source: GARLYN Scale via Home Assistant`. The three
custom-category records use `source: manual` because Sparky Mobile currently
prefills only manual custom measurements in its editable daily form. This is a
display-compatibility convention; the values are still generated and delivered
automatically by this integration.

Example with deliberately synthetic values:

```json
[
  {
    "type": "weight",
    "value": 74.8,
    "unit": "kg",
    "date": "2026-01-16",
    "timestamp": "2026-01-15T23:30:00Z",
    "record_timezone": "Europe/Amsterdam",
    "source": "GARLYN Scale via Home Assistant"
  },
  {
    "type": "body_fat_percentage",
    "value": 32.8,
    "unit": "%",
    "date": "2026-01-16",
    "timestamp": "2026-01-15T23:30:00Z",
    "record_timezone": "Europe/Amsterdam",
    "source": "GARLYN Scale via Home Assistant"
  },
  {
    "type": "body_fat_mass_kg",
    "value": 24.5,
    "unit": "kg",
    "date": "2026-01-16",
    "timestamp": "2026-01-15T23:30:00Z",
    "record_timezone": "Europe/Amsterdam",
    "source": "manual"
  }
]
```

The real request contains all seven records. The shortened example only shows
the common shape.

## Acceptance and retry semantics

Home Assistant returns `202 accepted` to the ESP only after it has atomically
persisted both the accepted GARLYN state and any new Sparky outbox work. This
response means the measurement is safe in Home Assistant; it does not mean the
background Sparky request has already completed.

A Sparky response is acknowledged only when all of these conditions hold:

- HTTP status is `200`;
- `errors` is an empty array;
- `skipped` is an empty array;
- `processed` contains exactly one successful result for every one of the seven
  expected type names.

Every other transport or per-record result remains queued. Retries start after
60 seconds and back off exponentially to a maximum interval of one hour. The
HTTP request timeout is 15 seconds.

Pending measurements are coalesced by stable profile ID and local date. If
several measurements are taken on one day, only the newest pending one is kept,
and a persistent date watermark prevents a delayed older measurement from
overwriting a newer value already accepted by Sparky. The queue holds at most
512 profile-days. In the exceptional case that it fills, the oldest pending day
is discarded so Sparky can never block Home Assistant measurement history.

## Privacy boundary

The persistent Sparky outbox contains only:

- the seven agreed numeric values;
- stable private profile ID and measurement ID;
- local date, UTC measurement time, and time-zone name;
- retry count, next retry time, and a sanitized error code.

It does not contain the API key, profile PIN or name, date of birth, sex, height,
Athlete Mode, raw BIA values, BMI, BMR, or bone mass. API keys remain in the
private Home Assistant config entry and are read only when a queued item is
sent. Logs never include keys, response bodies, URLs, or measurement values.

## Troubleshooting

- HTTP `401` or `403` in a `garlyn_scale` warning normally means the API key is
  wrong, disabled, expired, or lacks the required health-data write access.
- `partial_failure`, `invalid_response`, or `incomplete_response` means Sparky
  returned HTTP 200 but did not confirm all seven records. The complete batch is
  retried; native daily fields are safe to upsert again.
- Custom categories do not exist until the first request containing them has
  succeeded.
- A Home Assistant `202 accepted` with unchanged Sparky data is not data loss:
  it can indicate that the background request is waiting for its next retry.
- After correcting the URL, key, permissions, or Sparky availability, reload the
  integration or wait for the next scheduled retry. Pending work survives a
  Home Assistant restart.
