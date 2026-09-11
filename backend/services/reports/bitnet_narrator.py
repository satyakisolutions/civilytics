# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""BitNet report narrator — generates natural language report sections.

Uses Microsoft BitNet 1.58b (1-bit LLM) to generate contextual narratives
for weekly/monthly reports. Runs on CPU with ~2GB RAM — no GPU needed.

Instead of template-only text, BitNet writes:
    - Executive summary (2-3 paragraphs)
    - Per-camera analysis ("Camera X had a 40% increase in violations...")
    - Per-zone insights ("Zone A is the most active dumping hotspot...")
    - Trend analysis ("Violations are trending down week-over-week...")
    - Recommendations ("Consider adding a camera near the east gate...")
    - Anomaly detection ("Unusual spike at 3AM on Thursday — investigate")

Model: microsoft/BitNet-b1.58-2B-4T (2B params, 1.58-bit quantized)
Runtime: llama-cpp-python with GGUF format
Memory: ~2GB RAM on CPU
Speed: ~15-30 tokens/second on CPU (sufficient for reports)

Falls back to template text if BitNet is unavailable.
"""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MODELS_DIR = Path(os.environ.get("WW_MODELS_DIR", "/app/models/bitnet"))
MODEL_FILENAME = "BitNet-b1.58-2B-4T-Q4_K_M.gguf"
MODEL_PATH = MODELS_DIR / MODEL_FILENAME

# HuggingFace URL for the GGUF model
MODEL_URL = "https://huggingface.co/microsoft/BitNet-b1.58-2B-4T-GGUF/resolve/main/BitNet-b1.58-2B-4T-Q4_K_M.gguf"


class BitNetNarrator:
    """Generates natural language report narratives using BitNet.

    Falls back gracefully to template text if the model isn't available.
    """

    def __init__(self) -> None:
        self._llm = None
        self._available = False
        self._load_model()

    def _load_model(self) -> None:
        """Try to load the BitNet GGUF model via llama-cpp-python."""
        try:
            from llama_cpp import Llama
        except ImportError:
            logger.warning(
                "llama-cpp-python not installed — BitNet narrator disabled. "
                "Install: pip install llama-cpp-python"
            )
            return

        if not MODEL_PATH.exists():
            logger.warning(
                "BitNet model not found at %s — narrator disabled. "
                "Download: curl -L -o %s %s",
                MODEL_PATH, MODEL_PATH, MODEL_URL,
            )
            return

        try:
            self._llm = Llama(
                model_path=str(MODEL_PATH),
                n_ctx=4096,         # Context window
                n_threads=4,        # CPU threads
                n_gpu_layers=0,     # CPU only — no GPU needed
                verbose=False,
            )
            self._available = True
            logger.info("BitNet narrator loaded: %s", MODEL_PATH)
        except Exception:
            logger.exception("Failed to load BitNet model")

    @property
    def available(self) -> bool:
        return self._available

    def _generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Run inference on the prompt. Returns generated text."""
        if not self._available:
            return ""

        start = time.monotonic()
        try:
            result = self._llm(
                prompt,
                max_tokens=max_tokens,
                temperature=0.7,
                top_p=0.9,
                stop=["\n\n\n", "---", "END"],
            )
            text = result["choices"][0]["text"].strip()
            elapsed = time.monotonic() - start
            tokens = result["usage"]["completion_tokens"]
            logger.info(
                "BitNet generated %d tokens in %.1fs (%.1f tok/s)",
                tokens, elapsed, tokens / elapsed if elapsed > 0 else 0,
            )
            return text
        except Exception:
            logger.exception("BitNet generation failed")
            return ""

    # ------------------------------------------------------------------
    # Report narrative sections
    # ------------------------------------------------------------------

    def executive_summary(self, report: dict, report_type: str) -> str:
        """Generate an executive summary for the report."""
        data = self._build_data_context(report)
        prompt = f"""You are a surveillance analytics report writer for WasteWatch, an AI-powered waste dumping detection system deployed at Lake Shore Gardens, Bangalore.

Write a concise executive summary (2-3 paragraphs) for the {report_type} report.

Data:
{data}

Executive Summary:"""

        text = self._generate(prompt, max_tokens=300)
        if not text:
            return self._fallback_executive_summary(report, report_type)
        return text

    def camera_analysis(self, report: dict) -> str:
        """Generate per-camera analysis narrative."""
        cameras = report.get("by_camera", [])
        if not cameras:
            return "No camera-specific data available for this period."

        camera_text = "\n".join(
            f"- {c['camera']} ({c['zone']}): {c['count']} violations"
            for c in cameras[:10]
        )
        prompt = f"""Analyze the following camera violation data for a surveillance report. Identify the most and least active cameras, suggest reasons, and recommend actions.

Camera data:
{camera_text}

Total violations: {report.get('total_events', 0)}

Camera Analysis:"""

        text = self._generate(prompt, max_tokens=250)
        if not text:
            return self._fallback_camera_analysis(cameras)
        return text

    def zone_insights(self, report: dict) -> str:
        """Generate per-zone analysis."""
        zones = report.get("by_zone", [])
        if not zones:
            return "No zone data available."

        zone_text = "\n".join(
            f"- {z['zone']}: {z['count']} violations" for z in zones
        )
        prompt = f"""Analyze zone-level waste dumping patterns. Identify hotspots, quiet zones, and provide insights for resource allocation.

Zone data:
{zone_text}

Zone Analysis:"""

        text = self._generate(prompt, max_tokens=200)
        if not text:
            top = zones[0] if zones else {"zone": "unknown", "count": 0}
            return f"{top['zone']} is the most active zone with {top['count']} violations."
        return text

    def trend_analysis(self, report: dict) -> str:
        """Generate trend analysis from period comparison."""
        current = report.get("total_events", 0)
        previous = report.get("prev_period_events", 0)
        change = report.get("change_pct", 0)
        weekly_trend = report.get("weekly_trend", [])

        trend_text = ""
        if weekly_trend:
            trend_text = "Weekly trend:\n" + "\n".join(
                f"- {w['week_start']} to {w['week_end']}: {w['count']} events"
                for w in weekly_trend
            )

        prompt = f"""Analyze the violation trend for a surveillance report.

This period: {current} violations
Previous period: {previous} violations
Change: {change:+.1f}%
{trend_text}

Trend Analysis:"""

        text = self._generate(prompt, max_tokens=200)
        if not text:
            direction = "decreased" if change < 0 else "increased"
            return f"Violations {direction} by {abs(change):.1f}% compared to the previous period ({previous} → {current})."
        return text

    def recommendations(self, report: dict) -> str:
        """Generate actionable recommendations."""
        data = self._build_data_context(report)
        prompt = f"""Based on the following surveillance data, provide 3-5 actionable recommendations to reduce illegal waste dumping.

Data:
{data}

Recommendations:
1."""

        text = self._generate(prompt, max_tokens=300)
        if not text:
            return self._fallback_recommendations(report)
        return "1." + text if text else self._fallback_recommendations(report)

    def anomaly_detection(self, report: dict) -> str:
        """Detect and describe anomalies in the data."""
        hourly = report.get("hourly_distribution", [])
        if not hourly:
            return ""

        hourly_text = "\n".join(
            f"- {h['hour']}: {h['count']} violations" for h in hourly
        )
        prompt = f"""Identify any unusual patterns or anomalies in this hourly violation distribution. Note any unexpected spikes (e.g., late night activity) or unusual quiet periods.

Hourly data:
{hourly_text}

Anomalies:"""

        text = self._generate(prompt, max_tokens=200)
        return text  # OK to return empty — anomalies are optional

    # ------------------------------------------------------------------
    # Full narrative generation
    # ------------------------------------------------------------------

    def generate_full_narrative(self, report: dict, report_type: str) -> dict:
        """Generate all narrative sections for a report.

        Returns dict with keys: executive_summary, camera_analysis,
        zone_insights, trend_analysis, recommendations, anomalies.
        """
        start = time.monotonic()
        narrative = {
            "executive_summary": self.executive_summary(report, report_type),
            "camera_analysis": self.camera_analysis(report),
            "zone_insights": self.zone_insights(report),
            "trend_analysis": self.trend_analysis(report),
            "recommendations": self.recommendations(report),
            "anomalies": self.anomaly_detection(report),
            "generated_by": "BitNet-b1.58-2B-4T" if self._available else "template",
        }
        elapsed = time.monotonic() - start
        logger.info(
            "Full narrative generated in %.1fs (engine=%s)",
            elapsed, narrative["generated_by"],
        )
        return narrative

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_data_context(self, report: dict) -> str:
        """Build a text summary of report data for the prompt."""
        lines = [
            f"Period: {report.get('period_start', '?')} to {report.get('period_end', '?')}",
            f"Total violations: {report.get('total_events', 0)}",
            f"Previous period: {report.get('prev_period_events', 0)}",
            f"Change: {report.get('change_pct', 0):+.1f}%",
            f"Critical: {report.get('critical_count', 0)}",
            f"Warning: {report.get('warning_count', 0)}",
            f"VLM confirmed: {report.get('vlm_confirmed', 0)}/{report.get('vlm_total', 0)}",
            f"Unique plates: {report.get('unique_plates', 0)}",
            f"Avg response time: {report.get('avg_response_time_s', 0):.0f}s",
        ]
        cameras = report.get("by_camera", [])
        if cameras:
            lines.append(f"Most active camera: {cameras[0]['camera']} ({cameras[0]['count']} events)")
        zones = report.get("by_zone", [])
        if zones:
            lines.append(f"Most active zone: {zones[0]['zone']} ({zones[0]['count']} events)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Fallback templates (when BitNet unavailable)
    # ------------------------------------------------------------------

    def _fallback_executive_summary(self, report: dict, report_type: str) -> str:
        total = report.get("total_events", 0)
        prev = report.get("prev_period_events", 0)
        change = report.get("change_pct", 0)
        critical = report.get("critical_count", 0)
        direction = "decreased" if change < 0 else "increased"

        period = "week" if report_type == "weekly" else "month"
        return (
            f"During this {period}, the WasteWatch system recorded {total} violation events, "
            f"a {abs(change):.1f}% {direction} compared to the previous {period} ({prev} events). "
            f"Of these, {critical} were classified as critical severity. "
            f"The VLM confirmed dumping in {report.get('vlm_confirmed', 0)} of "
            f"{report.get('vlm_total', 0)} analyzed incidents. "
            f"{report.get('unique_plates', 0)} unique vehicle plates were captured."
        )

    def _fallback_camera_analysis(self, cameras: list[dict]) -> str:
        if not cameras:
            return "No camera data available."
        top = cameras[0]
        bottom = cameras[-1] if len(cameras) > 1 else None
        text = f"The most active camera was {top['camera']} in {top['zone']} with {top['count']} violations."
        if bottom and bottom["count"] < top["count"]:
            text += f" The least active was {bottom['camera']} with {bottom['count']} violations."
        return text

    def _fallback_recommendations(self, report: dict) -> str:
        recs = []
        cameras = report.get("by_camera", [])
        if cameras:
            recs.append(
                f"1. Focus patrol resources on {cameras[0]['camera']} ({cameras[0]['zone']}) — "
                f"highest violation count this period."
            )
        if report.get("avg_response_time_s", 0) > 300:
            recs.append(
                "2. Reduce alert response time — current average exceeds 5 minutes. "
                "Consider enabling SIP voice call escalation."
            )
        vlm_total = report.get("vlm_total", 0)
        vlm_confirmed = report.get("vlm_confirmed", 0)
        if vlm_total > 0 and (vlm_confirmed / vlm_total) < 0.5:
            recs.append(
                "3. High false positive rate in VLM analysis. "
                "Review ROI configurations and FSM dwell thresholds."
            )
        if not recs:
            recs.append("1. Continue monitoring. No significant issues detected this period.")
        return "\n".join(recs)
