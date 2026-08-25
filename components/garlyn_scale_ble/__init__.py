"""ESPHome BLE decoder for GARLYN Bodyscan Master scales."""

import esphome.config_validation as cv
from esphome import codegen as cg
from esphome.components import ble_client, http_request, time
from esphome.components.http_request import CONF_HTTP_REQUEST_ID, validate_url
from esphome.const import CONF_ID

DEPENDENCIES = ["ble_client", "http_request", "time"]

CONF_SCALE_ID = "scale_id"
CONF_TIME_ID = "time_id"
CONF_WEBHOOK_URL = "webhook_url"
CONF_RETRY_INTERVAL = "retry_interval"

garlyn_scale_ble_ns = cg.esphome_ns.namespace("garlyn_scale_ble")
GarlynScaleBle = garlyn_scale_ble_ns.class_(
    "GarlynScaleBle", cg.Component, ble_client.BLEClientNode
)


def _validate_scale_id(value: object) -> str:
    scale_id = cv.string_strict(value)
    if not scale_id or scale_id != scale_id.strip() or len(scale_id) > 64:
        raise cv.Invalid(
            "scale_id must be non-empty, trimmed, and at most 64 characters"
        )
    return scale_id


CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(GarlynScaleBle),
            cv.GenerateID(CONF_TIME_ID): cv.use_id(time.RealTimeClock),
            cv.GenerateID(CONF_HTTP_REQUEST_ID): cv.use_id(
                http_request.HttpRequestComponent
            ),
            cv.Required(CONF_SCALE_ID): _validate_scale_id,
            cv.Required(CONF_WEBHOOK_URL): validate_url,
            cv.Optional(
                CONF_RETRY_INTERVAL, default="60s"
            ): cv.positive_time_period_milliseconds,
        }
    )
    .extend(ble_client.BLE_CLIENT_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await ble_client.register_ble_node(var, config)

    clock = await cg.get_variable(config[CONF_TIME_ID])
    request = await cg.get_variable(config[CONF_HTTP_REQUEST_ID])
    cg.add(var.set_time(clock))
    cg.add(var.set_http_request(request))
    cg.add(var.set_scale_id(config[CONF_SCALE_ID]))
    cg.add(var.set_webhook_url(config[CONF_WEBHOOK_URL]))
    cg.add(
        var.set_retry_interval(config[CONF_RETRY_INTERVAL].total_milliseconds)
    )
