# ESPHome BLE transport

The `garlyn_scale_ble` external component is the first ESP-side transport
milestone. It subscribes to GARLYN service `FFF0`, characteristic `FFF3`,
reassembles a completed measurement, validates it, decodes the ten segmental
impedances, and logs an exact Home Assistant transport-v1 JSON object.

It deliberately does **not** send HTTP requests or persist a retry queue yet.
This keeps BLE/protocol validation separate from delivery behavior.

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
      ref: main
    components:
      - garlyn_scale_ble

ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"
    id: garlyn_ble
    auto_connect: true

time:
  - platform: homeassistant
    id: ha_time

garlyn_scale_ble:
  id: garlyn_decoder
  ble_client_id: garlyn_ble
  time_id: ha_time
  scale_id: "COPY_FROM_HA_CONNECTION_INFORMATION"
```

Keep the existing `FFF1` logger if it is still useful. Remove only the
`platform: ble_client` sensor whose characteristic is `FFF3`, because the new
component owns that subscription.

After a completed measurement, the expected log sequence is:

```text
[I][garlyn_scale_ble]: Decoded completed measurement for PIN ....: ... kg
[I][garlyn_scale_ble]: transport_v1: {"protocol_version":1,...}
```

The logged JSON is ready for the existing Home Assistant webhook parser, but
this milestone does not transmit it. No raw 68-byte frame is logged.

The committed C++ regression vector is explicitly synthetic. It uses profile
PIN `4242`, weight `74.8 kg`, and the same synthetic BIA values as the public
Home Assistant fixture; it is not a physical device capture.
