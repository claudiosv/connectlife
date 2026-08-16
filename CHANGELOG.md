# Changelog

All notable changes to this integration are documented here. Versions match
`custom_components/claudio_hisense/manifest.json`.

## 2.3.0

- Device fault flags (Upper/Lower Machine Fault, Indoor/Outdoor Coil/Temp
  Sensor Fault, Water Tank Full, Push Fault) now raise a Home Assistant
  repair issue (Settings → System → Repairs) and a persistent notification
  when they become active, and automatically clear both once ConnectLife
  reports the fault is no longer active.

## 2.2.0

- Added two general options: **Enable periodic cloud polling** (disables
  `DataUpdateCoordinator`'s recurring poll entirely, relying solely on
  WebSocket push updates) and **Re-poll the cloud shortly after sending a
  command** (disables the post-command confirmation poll in
  climate/fan/switch). Both default to on, matching prior behavior.

## 2.1.1

- Log the decoded WebSocket push content and resulting merged status
  alongside the existing "ConnectLife state updated via WebSocket push"
  debug message, instead of just the puid.

## 2.1.0

- Made external temperature/humidity sensor, Matter climate/temperature
  entity, target humidity, dry-idle mode, humidity hysteresis, thermostat
  forcing, and Matter sync timeout **per-device** instead of global — a
  single shared sensor/Matter entity didn't make sense once you have more
  than one ConnectLife AC. Configure them from the integration's
  **Configure** button, which now opens a picker between general settings
  and each AC.

## 2.0.2

- Repo tooling only: every future push now requires a version bump + this
  changelog to be updated (enforced by a Claude Code hook), and releases are
  tagged on GitHub so HACS shows the real version and release notes instead
  of a bare commit SHA.

## 2.0.1

- Fix `get_devices()`/`update_device()` reading a nonexistent `"response"`
  wrapper: the new API host returns `deviceList`/`kvMap` at the top level,
  not nested — devices were silently coming back empty despite a valid API
  response.

## 2.0.0

- **Breaking:** renamed the integration domain from
  `connectlife_claudio_wrapper` to `claudio_hisense`. Existing installs need
  to remove and re-add the integration.
- **Breaking:** replaced username/password login with a browser-based
  OAuth2 flow (no password stored in Home Assistant). The OAuth redirect
  URI is configurable at setup time, defaulting to
  `http://homeassistant.local:8123/auth/external/callback`.
- Added a WebSocket connection for real-time device-state push updates,
  alongside the existing polling.
- Fixed the wrong API host (`clife-eu-gateway.hijuconn.com` →
  `juapi-3rd.hijuconn.com`) causing every request to be rejected with a
  signature error.
- Fixed the WebSocket setup blocking Home Assistant's bootstrap for up to 5
  minutes on a slow/unresponsive ConnectLife server.
- Restructured the repo into `custom_components/claudio_hisense/` so HACS
  can install it.

## 1.1.1 and earlier

- Username/password (Gigya) login with RSA-signed requests; polling only.
