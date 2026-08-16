"""Constants for the ConnectLife integration."""

DOMAIN = "claudio_hisense"

CONF_BEEPING = "beeping"
CONF_DEVICES_CONFIG = "devices_config"
CONF_TEMPERATURE_UNIT = "temperature_unit"
CONF_TEMPERATURE_SENSORS = "temperature_sensors"
CONF_CURRENT_TEMP_ENTITY = "current_temperature_entity"
CONF_EXTERNAL_TEMP_ENABLED = "external_temp_enabled"
CONF_THERMOSTAT_FORCING_ENABLED = "thermostat_forcing_enabled"
CONF_CURRENT_HUMIDITY_ENTITY = "current_humidity_entity"
CONF_TARGET_HUMIDITY = "target_humidity"
CONF_DRY_IDLE_MODE = "dry_idle_mode"
CONF_HUMIDITY_HYSTERESIS = "humidity_hysteresis"
CONF_MATTER_CLIMATE_ENTITY = "matter_climate_entity"
CONF_MATTER_TEMPERATURE_SENSOR_ENTITY = "matter_temperature_sensor_entity"
CONF_MATTER_SYNC_TIMEOUT = "matter_sync_timeout"
CONF_SENSOR_CONTROL_MIN_INTERVAL = "sensor_control_min_interval"
CONF_TEMPERATURE_PRECISION = "temperature_precision"
CONF_HUMIDITY_PRECISION = "humidity_precision"
CONF_POLL_INTERVAL = "poll_interval"
CONF_COMMAND_REFRESH_DELAY = "command_refresh_delay"
CONF_DEBOUNCE_DELAY = "debounce_delay"
CONF_DEBUG_LOGGING = "debug_logging"
CONF_OAUTH_REDIRECT_URI = "oauth_redirect_uri"
# Per-device overrides, keyed by puid: {puid: {CONF_CURRENT_TEMP_ENTITY: ..., ...}}.
# Holds the entity-linking options below — a single global sensor/Matter
# entity doesn't make sense once there's more than one AC.
CONF_DEVICES = "devices"

TEMP_UNIT_CELSIUS = "celsius"
TEMP_UNIT_FAHRENHEIT = "fahrenheit"

# Package log level override (see __init__._apply_debug_logging). "default"
# leaves Home Assistant's own configured level in place.
LOG_LEVEL_DEFAULT = "default"
LOG_LEVEL_INFO = "info"
LOG_LEVEL_DEBUG = "debug"

# What to do with the linked Matter device once the dry-mode humidity target
# is reached (see DRY_IDLE_MODE_* below).
DRY_IDLE_MODE_FAN_ONLY = "fan_only"
DRY_IDLE_MODE_OFF = "off"
# Percentage points of slack around the target before switching dry<->idle,
# so humidity hovering right at the target doesn't cause rapid cycling.
DEFAULT_HUMIDITY_HYSTERESIS = 3

UPDATE_INTERVAL_SECONDS = 60
COMMAND_REFRESH_DELAY_SECONDS = 5  # seconds after a command before re-polling the cloud
DEBOUNCE_DELAY_SECONDS = 3  # seconds of inactivity before batched commands are sent
# How long to trust our own optimistic state after a Matter-redirected command
# before giving up and accepting whatever ConnectLife reports, even if it
# still disagrees (ConnectLife's cloud can be slow to learn about a change
# made directly on the Matter side).
MATTER_SYNC_TIMEOUT_SECONDS = 60
# Minimum time between _async_control() runs triggered by external sensor
# updates (temp/humidity sensors can report every few seconds, far more
# often than the thermostat loop needs to react).
SENSOR_CONTROL_MIN_INTERVAL_SECONDS = 30
ENERGY_CACHE_SECONDS = 600  # 10 minutes

# Rate-limit / retry behaviour
RETRY_ATTEMPTS = 3  # max attempts per request before giving up
RETRY_BACKOFF_BASE = 2.0  # seconds; delay = base * 2^attempt
RETRY_BACKOFF_MAX = 60.0  # cap on computed delay
ENERGY_REQUEST_DELAY = 1.0  # seconds between per-device energy calls

AC_DEVICE_TYPE_CODES = {"009", "006", "008"}

# ConnectLife API. This is the plugin's API_BASE_URL, used for every
# HMAC-signed request (device list/control, websocket phone-code
# registration, etc.) — NOT clife-eu-gateway.hijuconn.com, which was the
# old RSA-signing scheme's host and doesn't recognize the new CLIENT_ID's
# HMAC-signed requests (server rejects them with "Parameter Error : sign").
BASE_URL = "https://juapi-3rd.hijuconn.com"

# OAuth2 (browser-redirect login against the same backend Hisense's own
# "Hisense AC" app registration uses — see Connectlife-LLC/HomeAssistantPlugin).
OAUTH2_AUTHORIZE = "https://oauth.hijuconn.com/login"
OAUTH2_TOKEN = "https://oauth.hijuconn.com/oauth/token"
CLIENT_ID = "9793620883275788"
CLIENT_SECRET = "7h1m3gZVlILyBvIFBNmzXwoFYLhkGqG9NQd2jBzuZCqJKCTyCtYwQtXi4tVBjg9B"
# ConnectLife's OAuth server only accepts a fixed, pre-registered redirect
# URI — it will not redirect to Home Assistant's own dynamically-computed
# external URL, unlike most OAuth2 integrations. Configurable at setup time
# (the config flow shows this as an editable field) in case a deployment
# needs a different host/port than the plugin's original hardcoded default.
DEFAULT_OAUTH_REDIRECT_URI = "http://homeassistant.local:8123/auth/external/callback"

# WebSocket push updates
WEBSOCKET_RECONNECT_INTERVAL = 30  # seconds

# Temperature unit codes (from ConnectLife API PHP enum: celsius='0', fahrenheit='1')
TEMP_CODE_CELSIUS = 0
TEMP_CODE_FAHRENHEIT = 1

# t_work_mode values
WORK_MODE_FAN_ONLY = "0"
WORK_MODE_HEAT = "1"
WORK_MODE_COOL = "2"
WORK_MODE_DRY = "3"
WORK_MODE_AUTO = "4"

# Matter (vendor_id, product_id) pairs known to support DRY/FAN_ONLY modes but missing
# from Home Assistant's built-in Matter climate allowlist. Combined at runtime with
# devices detected via f_matterOriginalVendorId/f_matterOriginalProductId.
MATTER_DRY_FAN_DEVICES: set[tuple[int, int]] = {
    (0x138C, 0x3601),
}

DEFAULT_DEVICES_CONFIG = {
    "t_work_mode": ["fan only", "heat", "cool", "dry", "auto"],
    "t_fan_speed": {
        "0": "auto",
        "5": "super low",
        "6": "low",
        "7": "medium",
        "8": "high",
        "9": "super high",
    },
}
