"""compose_report — three-stage incident report composition."""
from datetime import datetime, timezone

import httpx
from workers.http_client import service_client
import structlog

from workers.config import settings

log = structlog.get_logger()


def _analyze_evidence(event: dict) -> str:
    refs = event.get("evidence_refs", [])
    lpr = event.get("lpr_result") or {}
    plates = lpr.get("plates", [])
    images = [r for r in refs if any(r.endswith(ext) for ext in (".jpg", ".jpeg", ".png"))]
    videos = [r for r in refs if r.endswith(".mp4")]

    parts = [f"{len(refs)} evidence file(s) collected"]
    if images:
        parts.append(f"{len(images)} image(s)")
    if videos:
        parts.append(f"{len(videos)} video clip(s)")
    if plates:
        parts.append(f"License plate(s) identified: {', '.join(plates)}")
    else:
        parts.append("No license plates captured")
    return ". ".join(parts) + "."


def _assess_severity(event: dict) -> tuple[str, str]:
    confidence = event.get("confidence", 0)
    priority = (event.get("priority") or "medium").lower()
    cluster = event.get("cluster_result") or {}
    is_repeat = cluster.get("match_count", 0) > 0

    score = 0
    reasons = []

    if confidence >= 0.9:
        score += 3
        reasons.append(f"high confidence ({confidence:.0%})")
    elif confidence >= 0.7:
        score += 2
        reasons.append(f"moderate confidence ({confidence:.0%})")
    else:
        score += 1
        reasons.append(f"low confidence ({confidence:.0%})")

    if priority == "critical":
        score += 3
        reasons.append("critical priority")
    elif priority == "high":
        score += 2
        reasons.append("high priority")

    if is_repeat:
        score += 2
        reasons.append(f"repeat offender ({cluster.get('match_count', 0)} prior match(es))")

    if score >= 7:
        level = "critical"
    elif score >= 5:
        level = "high"
    elif score >= 3:
        level = "medium"
    else:
        level = "low"

    return level, "Severity based on: " + "; ".join(reasons) + "."


def _recommendations(event: dict, severity_level: str) -> list[str]:
    recommendations = []
    if severity_level in ("critical", "high"):
        recommendations.append("Dispatch field team for immediate inspection")
    if severity_level == "critical":
        recommendations.append("Notify municipal enforcement authority")
    cluster = event.get("cluster_result") or {}
    if cluster.get("match_count", 0) > 0:
        recommendations.append("Flag location for increased surveillance")
    lpr = event.get("lpr_result") or {}
    if lpr.get("plates"):
        recommendations.append("Cross-reference plate(s) with prior violation records")
    if not recommendations:
        recommendations.append("Log for review during next scheduled patrol")
    return recommendations


_FALLBACK_NARRATIVE = (
    "An incident of type '{detection}' was detected by camera {camera} at {ts}. "
    "{evidence_summary} {rationale} Overall severity: {severity}."
)


async def _compose_narrative(event: dict, evidence_summary: str, severity_level: str, rationale: str) -> tuple[str, str]:
    """Write the incident prose via the backend's BitNet narrator.

    Only code-derived facts are sent — BitNet never sees raw evidence, so it
    cannot invent a plate, identity, or detail that isn't already in `facts`.
    Falls back to a plain template on any HTTP/model failure so report
    generation never blocks on the narrator being down.
    """
    detection = (event.get("detection_type") or "unknown").replace("_", " ").title()
    camera = event.get("camera_id", "unknown")
    ts = event.get("timestamp", "unknown time")
    cluster = event.get("cluster_result") or {}
    lpr = event.get("lpr_result") or {}

    facts = {
        "detection_type": event.get("detection_type") or "unknown",
        "camera_id": camera,
        "timestamp": ts,
        "severity_level": severity_level,
        "plates": lpr.get("plates", []),
        "is_repeat_offender": cluster.get("match_count", 0) > 0,
        "repeat_match_count": cluster.get("match_count", 0),
        "cluster_hotspot": bool(cluster.get("is_hotspot")),
    }

    fallback = _FALLBACK_NARRATIVE.format(
        detection=detection, camera=camera, ts=ts,
        evidence_summary=evidence_summary, rationale=rationale,
        severity=severity_level.upper(),
    )

    try:
        async with service_client(timeout=30) as client:
            resp = await client.post(
                f"{settings.reports_service_url}/reports/incident-narrative",
                json=facts,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("narrative") or fallback, data.get("generated_by", "template")
    except httpx.HTTPError:
        log.warning("report.narrative_http_failed", event_id=event.get("event_id"))
        return fallback, "template"


async def handle_compose_report(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    log.info("report.composing", event_id=event_id)

    evidence_summary = _analyze_evidence(event)
    severity_level, rationale = _assess_severity(event)
    narrative, model = await _compose_narrative(event, evidence_summary, severity_level, rationale)
    recommendations = _recommendations(event, severity_level)

    detection = (event.get("detection_type") or "Incident").replace("_", " ").title()
    camera = event.get("camera_id", "unknown")

    report = {
        "event_id": event_id,
        "title": f"{detection} — Camera {camera}",
        "severity_level": severity_level,
        "evidence_summary": evidence_summary,
        "severity_rationale": rationale,
        "narrative": narrative,
        "recommendations": recommendations,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }

    log.info("report.composed", event_id=event_id, severity=severity_level)
    return {"status": "COMPLETED", "report": report}
