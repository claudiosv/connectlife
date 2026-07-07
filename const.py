"""Constants for the ConnectLife integration."""

DOMAIN = "connectlife_claudio_wrapper"

CONF_BEEPING = "beeping"
CONF_DEVICES_CONFIG = "devices_config"
CONF_TEMPERATURE_UNIT = "temperature_unit"
CONF_TEMPERATURE_SENSORS = "temperature_sensors"
CONF_CURRENT_TEMP_ENTITY = "current_temperature_entity"
CONF_EXTERNAL_TEMP_ENABLED = "external_temp_enabled"
CONF_CURRENT_HUMIDITY_ENTITY = "current_humidity_entity"
CONF_TARGET_HUMIDITY = "target_humidity"
CONF_DRY_IDLE_MODE = "dry_idle_mode"
CONF_HUMIDITY_HYSTERESIS = "humidity_hysteresis"
CONF_MATTER_CLIMATE_ENTITY = "matter_climate_entity"
CONF_MATTER_TEMPERATURE_SENSOR_ENTITY = "matter_temperature_sensor_entity"
CONF_MATTER_SYNC_TIMEOUT = "matter_sync_timeout"
CONF_TEMPERATURE_PRECISION = "temperature_precision"
CONF_HUMIDITY_PRECISION = "humidity_precision"
CONF_POLL_INTERVAL = "poll_interval"
CONF_COMMAND_REFRESH_DELAY = "command_refresh_delay"
CONF_DEBOUNCE_DELAY = "debounce_delay"
CONF_DEBUG_LOGGING = "debug_logging"

TEMP_UNIT_CELSIUS = "celsius"
TEMP_UNIT_FAHRENHEIT = "fahrenheit"

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
TOKEN_CACHE_SECONDS = 86400  # 24 hours
ENERGY_CACHE_SECONDS = 600  # 10 minutes

# Rate-limit / retry behaviour
RETRY_ATTEMPTS = 3  # max attempts per request before giving up
RETRY_BACKOFF_BASE = 2.0  # seconds; delay = base * 2^attempt
RETRY_BACKOFF_MAX = 60.0  # cap on computed delay
ENERGY_REQUEST_DELAY = 1.0  # seconds between per-device energy calls

AC_DEVICE_TYPE_CODES = {"009", "006", "008"}

# ConnectLife API
BASE_URL = "https://clife-eu-gateway.hijuconn.com"
GIGYA_API_KEY = "4_yhTWQmHFpZkQZDSV1uV-_A"
GIGYA_GMID = (
    "gmid.ver4.AtLt3mZAMA.C8m5VqSTEQDrTRrkYYDgOaJWcyQ-XHow5nzQSXJF3EO3TnqTJ8tKUmQaaQ6z8p0s"
    ".zcTbHe6Ax6lHfvTN7JUj7VgO4x8Vl-vk1u0kZcrkKmKWw8K9r0shyut_at5Q0ri6zTewnAv2g1Dc8dauuyd-Sw.sc3"
)
OAUTH_CLIENT_ID = "5065059336212"
OAUTH_CLIENT_SECRET = "07swfKgvJhC3ydOUS9YV_SwVz0i4LKqlOLGNUukYHVMsJRF1b-iWeUGcNlXyYCeK"
OAUTH_REDIRECT_URI = "https://api.connectlife.io/swagger/oauth2-redirect.html"

APP_ID = "47110565134383"
APP_SECRET = "yOzhz6junYno-nmULM3Wr7PU_dpSZN22ZdluvVWZ4uW5ZwwG8fIGCHTbrhcnU-iv"
SIGN_MAGIC = "D9519A4B756946F081B7BB5B5E8D1197"

PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyyWrNG6q475HIHu7sMVu
vHof6vlgPeixmxa4EL/UsvVvHPz33NnWoQetQqit9TBNzUjMXw0KlY9PXM4iqHUU
U+dSyNDq1jZWIiJ2C2FccppswJtIKL3NRMFvT9PFh6NlP/4FUcQKojgKFbF7Kacc
JPKYHlwaO7qgoIjLxAHlSOXGpucJcOkPzT2EqsSVnW8sn8kenvNmghXDayhgxsh6
AyxK4kehJplEnmX/iYCfNoFXknGcLqFWYccgBz3fybvx30C/0IgU1980L8QsUAv5
esZmN8ugnbRgLRxKRlkQQLxQAiZMZdKTAx665YflT3YMHJvEFE8c2XFgoxHzSMc4
BwIDAQAB
-----END PUBLIC KEY-----
"""

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
