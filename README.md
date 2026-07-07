# ConnectLife Home Assistant Integration

A native Python Home Assistant custom integration for ConnectLife AC devices.
**No MQTT broker and no PHP required.**

## Features

- **Climate entity** per AC device — control power, HVAC mode, target temperature, fan speed, and swing
- **Eco preset** — toggles the `t_eco` property on the device
- **Energy sensor** — daily kWh consumption (where the API provides it)
- **Config Flow UI** — set up entirely through the Home Assistant UI
- Polls every 60 seconds; access token cached for 24 hours

## Supported devices

| deviceTypeCode | Description |
|---|---|
| `009` | Split AC |
| `006` | Portable AC |
| `008` | Window unit AC |

## Installation

### Via HACS (recommended)

1. Add this repository as a custom HACS integration repository.
2. Search for **ConnectLife** and install.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for **ConnectLife**.

### Manual

1. Copy the `custom_components/connectlife` directory into your
   `<config>/custom_components/` folder.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for **ConnectLife**.

## Configuration

| Field | Required | Default | Description |
|---|---|---|---|
| Email address | Yes | — | Your ConnectLife account email |
| Password | Yes | — | Your ConnectLife account password |
| Enable beep on commands | No | `false` | Whether the AC beeps when a command is sent |
| Temperature unit | No | `celsius` | `celsius` or `fahrenheit` |
| Device configuration (JSON) | No | See below | Per-device customisation of modes, fan speeds, and swing options |

### Device configuration JSON

The device configuration is keyed by **deviceFeatureCode** (e.g. `"117"`).
If a device's feature code is not found in the JSON the integration falls back to a built-in default.

```json
{
  "117": {
    "t_work_mode": ["fan only", "heat", "cool", "dry", "auto"],
    "t_fan_speed": {
      "0": "auto",
      "5": "super low",
      "6": "low",
      "7": "medium",
      "8": "high",
      "9": "super high"
    },
    "t_swing_direction": ["straight", "right", "both sides", "swing", "left"],
    "t_swing_angle": {
      "0": "swing",
      "2": "bottom 1/6",
      "3": "bottom 2/6",
      "4": "bottom 3/6",
      "5": "top 4/6",
      "6": "top 5/6",
      "7": "top 6/6"
    }
  }
}
```

## Dependencies

- `cryptography` >= 42.0.0 (listed in `manifest.json`; installed automatically by HA)

## How it works

1. Authenticates via Gigya (SAP CDC) using your ConnectLife credentials.
2. Exchanges the Gigya JWT for an OAuth access token from `oauth.hijuconn.com`.
3. Signs every request to the ConnectLife gateway using RSA-encrypted SHA-256.
4. Polls the device list every 60 seconds and exposes each AC as a HA climate entity.
5. Sends property-update requests directly to the ConnectLife API when you change a setting in HA.
