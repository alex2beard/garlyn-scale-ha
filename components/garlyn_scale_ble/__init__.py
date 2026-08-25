"""ESPHome BLE decoder for GARLYN Bodyscan Master scales."""

import esphome.config_validation as cv
from esphome import codegen as cg
from esphome.components import ble_client, time
from esphome.const import CONF_ID

DEPENDENCIES = ["ble_client", "time"]

CONF_SCALE_ID = "scale_id"
CONF_TIME_ID = "time_id"

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
            cv.Required(CONF_SCALE_ID): _validate_scale_id,
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
    cg.add(var.set_time(clock))
    cg.add(var.set_scale_id(config[CONF_SCALE_ID]))
