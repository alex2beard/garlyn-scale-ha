"""Constants for the GARLYN Scale integration."""

DOMAIN = "garlyn_scale"

CONF_SCALE_ID = "scale_id"
CONF_SCALE_NAME = "scale_name"
CONF_WEBHOOK_ID = "webhook_id"
CONF_PROFILES = "profiles"

CONF_PROFILE_NAME = "name"
CONF_PROFILE_ID = "profile_id"
CONF_PROFILE_PIN = "profile_pin"
CONF_SEX = "sex"
CONF_DATE_OF_BIRTH = "date_of_birth"
CONF_HEIGHT_CM = "height_cm"
CONF_ATHLETE_MODE = "athlete_mode"
CONF_REFERENCE_STANDARD = "reference_standard"

DEFAULT_SCALE_NAME = "GARLYN Bodyscan Master"
TRANSPORT_PROTOCOL_VERSION = 1
ALGORITHM_VERSION = "bodyFatScaleAlg_2.2.2"

MAX_WEBHOOK_PAYLOAD_BYTES = 16_384
DEFAULT_DEDUPLICATION_CACHE_SIZE = 256
