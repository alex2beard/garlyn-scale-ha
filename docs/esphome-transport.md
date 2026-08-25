# ESPHome BLE transport

The `garlyn_scale_ble` external component subscribes to GARLYN service `FFF0`,
characteristic `FFF3`, reassembles a completed measurement, validates it,
decodes the ten segmental impedances, and delivers an exact Home Assistant
transport-v1 JSON object through the integration webhook.

Delivery is at least once. The component stores a payload in ESP32 NVS before
the first HTTP attempt and retains it across restarts until Home Assistant
acknowledges it.

## Confirmed 68-byte measurement frame

| Offset | Size | Meaning |
| ---: | ---: | --- |
| 0 | 2 | Magic `33 CC` |
| 2 | 1 | Total frame length, `44` hexadecimal / 68 decimal |
| 8 | 2 | Four profile digits, packed BCD |
| 10 | 2 | Weight, unsigned big-endian integer divided by 100 kg |
| 24 | 4 | Right arm, 20 kHz |
| 28 | 4 | Left arm, 20 kHz |
| 32 | 4 | Trunk, 20 kHz |
| 36 | 4 | Right leg, 20 kHz |
| 40 | 4 | Left leg, 20 kHz |
| 44 | 4 | Right arm, 100 kHz |
| 48 | 4 | Left arm, 100 kHz |
| 52 | 4 | Trunk, 100 kHz |
| 56 | 4 | Right leg, 100 kHz |
| 60 | 4 | Left leg, 100 kHz |
| 67 | 1 | Checksum |

The checksum rule is:

```text
(sum(frame[0..66]) + 1) & 0xFF == frame[67]
```

Each four-byte impedance field is decoded by reading an unsigned 32-bit
big-endian word, swapping its 16-bit halves, interpreting the resulting bits as
IEEE-754 binary32, and dividing by 10. The component then changes packet order
to the transport-v1 order:

```text
left_arm, right_arm, left_leg, right_leg, trunk
```

## ESPHome configuration

Use the same `ble_client` that already connects to the scale. Replace the old
raw `FFF3` BLE sensor/logger with this component so that only one node subscribes
to the indication characteristic.

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/alex2beard/garlyn-scale-ha
      ref: v0.5.0
    components:
      - garlyn_scale_ble

ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"
    id: garlyn_ble
    auto_connect: true

time:
  - platform: homeassistant
    id: ha_time

http_request:
  id: garlyn_http
  timeout: 10s

garlyn_scale_ble:
  id: garlyn_decoder
  ble_client_id: garlyn_ble
  time_id: ha_time
  http_request_id: garlyn_http
  scale_id: "COPY_FROM_HA_CONNECTION_INFORMATION"
  webhook_url: >-
    http://homeassistant.local:8123/api/webhook/COPY_FROM_HA_CONNECTION_INFORMATION
  retry_interval: 60s
```

Pin `ref` to an exact release tag. This keeps a working ESPHome build stable
when development continues on `main`; update the tag intentionally when moving
to a newer release.

`webhook_url` must contain the complete local URL. Copy its path from
**Configure -> Connection information** in Home Assistant and prepend the
internal Home Assistant base URL. Treat the resulting URL as private because
the random webhook ID authorizes delivery to this integration entry.

Keep the existing `FFF1` logger if it is still useful. Remove only the
`platform: ble_client` sensor whose characteristic is `FFF3`, because the new
component owns that subscription.

After a completed measurement, the expected log sequence is:

```text
[I][garlyn_scale_ble]: Decoded completed measurement for PIN ....: ... kg
[I][garlyn_scale_ble]: Queued GARLYN measurement for delivery; pending=1
[I][garlyn_scale_ble]: Webhook acknowledged measurement with HTTP 202; pending=0
```

The full `transport_v1` JSON is logged only at `VERBOSE` level. No raw 68-byte
frame is logged.

## Persistent delivery behavior

The NVS queue is FIFO and holds eight measurements. Its behavior is:

- enqueue and synchronize NVS before attempting HTTP;
- remove the oldest item only for HTTP `202 accepted` or `200 duplicate`;
- retain and retry after connection failures and every other HTTP status,
  including `409` while a profile is missing or temporarily invalid;
- retry the oldest item after every restart using the same `measurement_id`;
- preserve older pending measurements if the queue is full and reject the
  newest one with a warning rather than silently overwriting unacknowledged
  data.

The queue key is derived from `scale_id`. Changing `scale_id` therefore starts
a separate queue and should only be done when replacing the Home Assistant
integration entry.

The committed C++ regression vector is explicitly synthetic. It uses profile
PIN `4242`, weight `74.8 kg`, and the same synthetic BIA values as the public
Home Assistant fixture; it is not a physical device capture.
