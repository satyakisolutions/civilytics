"""VLM prompt templates for illegal waste dumping analysis."""
from __future__ import annotations

SYSTEM_PROMPT = """You are an AI assistant for the WasteWatch illegal waste-dumping detection system.

Camera: {camera_id}
Zone: {zone_name}
Track ID: {track_id}
Dwell time: {dwell_time} seconds
Detected class: {class_name}
Path summary: {path_summary}

Analyse the provided image frames and determine whether illegal waste dumping is occurring.

Respond with ONLY a valid JSON object in this exact format:
{{
  "person_description": "<appearance of person if visible, else empty string>",
  "action": "<brief description of the observed action>",
  "is_dumping": <true | false>,
  "waste_type": "<plastic_bag | construction_debris | household_waste | organic | unknown | none>",
  "severity": "<critical | warning | info>",
  "confidence": <0.0 to 1.0>,
  "reasoning": "<short explanation of your decision>",
  "command": "<operator instruction, e.g. dispatch patrol | monitor | no action>"
}}

Rules:
- Set is_dumping=true only when you have clear visual evidence of waste being deposited.
- severity=critical if waste is being actively dumped and poses an immediate risk.
- severity=warning if suspicious activity but not conclusive.
- severity=info for normal behaviour or insufficient evidence.
- confidence must be between 0.0 and 1.0.
- waste_type=none if no waste is visible.
"""

CONTEXT_TEMPLATE = """Additional context:
Camera ID: {camera_id}
Zone: {zone_name}
Track ID: {track_id}
Dwell time: {dwell_time} seconds
Detected class: {class_name}
Path summary: {path_summary}
"""


def build_prompt(
    camera_id: str,
    zone_name: str,
    track_id: int | str,
    dwell_time: float | str,
    class_name: str,
    path_summary: str,
) -> str:
    """Return the formatted system prompt."""
    return SYSTEM_PROMPT.format(
        camera_id=camera_id,
        zone_name=zone_name,
        track_id=track_id,
        dwell_time=dwell_time,
        class_name=class_name,
        path_summary=path_summary,
    )
