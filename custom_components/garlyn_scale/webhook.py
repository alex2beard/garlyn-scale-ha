"""Home Assistant webhook adapter for normalized GARLYN measurements."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from http import HTTPStatus

from aiohttp import ContentTypeError, web

from .const import MAX_WEBHOOK_PAYLOAD_BYTES
from .runtime import (
    AcceptanceStatus,
    InvalidProfileForMeasurementError,
    ScaleRuntime,
    UnknownProfileError,
)
from .transport import TransportValidationError, parse_measurement


async def async_handle_measurement(
    runtime: ScaleRuntime,
    request: web.Request,
    async_save_state: Callable[[ScaleRuntime], Awaitable[None]] | None = None,
) -> web.Response:
    """Validate and accept one ESP transport request."""
    if (
        request.content_length is not None
        and request.content_length > MAX_WEBHOOK_PAYLOAD_BYTES
    ):
        return web.json_response(
            {"status": "error", "error": "payload_too_large"},
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    try:
        payload = await request.json()
    except (ContentTypeError, json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response(
            {"status": "error", "error": "invalid_json"},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        measurement = parse_measurement(payload, expected_scale_id=runtime.scale_id)
    except TransportValidationError as err:
        return web.json_response(
            {"status": "error", "error": "invalid_measurement", "detail": str(err)},
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
        )

    try:
        status = runtime.process(measurement)
    except UnknownProfileError as err:
        return web.json_response(
            {
                "status": "error",
                "error": "unknown_profile",
                "profile_pin": err.profile_pin,
            },
            status=HTTPStatus.CONFLICT,
        )
    except InvalidProfileForMeasurementError as err:
        return web.json_response(
            {
                "status": "error",
                "error": "profile_invalid_for_measurement",
                "profile_pin": err.profile_pin,
            },
            status=HTTPStatus.CONFLICT,
        )

    # Save accepted state before acknowledging it. Duplicate deliveries are
    # saved too: this persists their LRU touch and lets a retry repair state if
    # an earlier save raised before its HTTP response was sent.
    if async_save_state is not None:
        await async_save_state(runtime)
    runtime.publish_last_processed()

    response_status = (
        HTTPStatus.OK if status is AcceptanceStatus.DUPLICATE else HTTPStatus.ACCEPTED
    )
    return web.json_response(
        {"status": status.value, "measurement_id": measurement.measurement_id},
        status=response_status,
    )
