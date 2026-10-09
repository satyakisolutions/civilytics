"""validate_dumping_event — schema and reference validation."""
import structlog

log = structlog.get_logger()

VALID_DETECTION_TYPES = {"MIDNIGHT_WALK", "MIDNIGHT_VEHICLE", "DAYTIME_WALK", "DAYTIME_VEHICLE"}


async def handle_validate(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", input_data)

    required = [
        "event_id",
        "event_occurred_at",
        "workspace_id",
        "zone_id",
        "camera_id",
        "detection_type",
        "confidence",
        "priority",
        "source_video_ref",
        "source_frame_ref",
    ]
    missing = [f for f in required if not event.get(f)]
    if missing:
        raise Exception(f"terminal: missing required fields: {', '.join(missing)}")

    if event.get("detection_type") not in VALID_DETECTION_TYPES:
        raise Exception(
            f"terminal: invalid detection_type: {event.get('detection_type')}"
        )

    confidence = event.get("confidence", 0)
    if not (0 <= confidence <= 1):
        raise Exception(f"terminal: confidence out of range: {confidence}")

    log.info(
        "event.validated",
        event_id=event.get("event_id"),
        detection_type=event.get("detection_type"),
    )

    return {
        "validated_event": event,
        "validation_status": "PASSED",
        "vehicle_visible": bool(event.get("vehicle_visible")),
        "person_visible": bool(event.get("person_visible")),
    }
