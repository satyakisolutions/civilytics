"""compose_report — three-stage incident report composition."""
# ponytail: upgrade to CrewAI + BitNet b1.58 2B4T when model package available
from datetime import datetime, timezone

import structlog

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


def _compose_narrative(event: dict, evidence_summary: str, severity_level: str, rationale: str) -> tuple[str, list[str]]:
    detection = (event.get("detection_type") or "unknown").replace("_", " ").title()
    camera = event.get("camera_id", "unknown")
    ts = event.get("timestamp", "unknown time")

    narrative = (
        f"An incident of type '{detection}' was detected by camera {camera} "
        f"at {ts}. {evidence_summary} {rationale} "
        f"Overall severity: {severity_level.upper()}."
    )

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

    return narrative, recommendations


async def handle_compose_report(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    log.info("report.composing", event_id=event_id)

    evidence_summary = _analyze_evidence(event)
    severity_level, rationale = _assess_severity(event)
    narrative, recommendations = _compose_narrative(event, evidence_summary, severity_level, rationale)

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
        "model": "rule-based-v1",
    }

    log.info("report.composed", event_id=event_id, severity=severity_level)
    return {"status": "COMPLETED", "report": report}
