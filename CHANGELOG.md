# Changelog

All notable changes to this integration are documented here. Versions match
`custom_components/claudio_hisense/manifest.json`.

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
