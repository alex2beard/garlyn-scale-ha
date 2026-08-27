"""Home Assistant webhook adapter for normalized GARLYN measurements."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from http import HTTPStatus

from aiohttp import ContentTypeError, web

from .const import MAX_WEBHOOK_PAYLOAD_BYTES
from .runtime import (
    AcceptanceStatus,
    InvalidProfileForMeasurementError,
    ProcessedMeasurement,
    ScaleRuntime,
    UnknownProfileError,
)
from .transport import TransportValidationError, parse_measurement

type RollbackCallback = Callable[[], None]
type PrepareStateCallback = Callable[[ProcessedMeasurement], RollbackCallback | None]


async def async_handle_measurement(
    runtime: ScaleRuntime,
    request: web.Request,
    async_save_state: Callable[[ScaleRuntime], Awaitable[None]] | None = None,
    prepare_state: PrepareStateCallback | None = None,
    state_lock: asyncio.Lock | None = None,
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

    lock = state_lock or asyncio.Lock()
    async with lock:
        checkpoint = runtime.checkpoint()
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

        rollback_prepared_state: RollbackCallback | None = None
        try:
            if status is AcceptanceStatus.ACCEPTED and prepare_state is not None:
                processed = runtime.last_processed_measurement
                assert processed is not None
                rollback_prepared_state = prepare_state(processed)

            # Save accepted HA state and its optional downstream outbox together
            # before acknowledging the ESP. Sparky network I/O happens later.
            if async_save_state is not None:
                await async_save_state(runtime)
        except BaseException:
            if rollback_prepared_state is not None:
                rollback_prepared_state()
            runtime.restore_checkpoint(checkpoint)
            raise
        runtime.publish_last_processed()

    response_status = (
        HTTPStatus.OK if status is AcceptanceStatus.DUPLICATE else HTTPStatus.ACCEPTED
    )
    return web.json_response(
        {"status": status.value, "measurement_id": measurement.measurement_id},
        status=response_status,
    )
