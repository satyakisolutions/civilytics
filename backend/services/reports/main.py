# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Report Generation Service — weekly and monthly email reports.

Generates HTML reports with analytics data and sends via SMTP.

Schedule:
    Weekly report:  Every Monday at 8:00 AM IST
    Monthly report: 1st of every month at 8:00 AM IST

Reports include:
    - Total violations (period + comparison to previous period)
    - Violations by camera / zone (table + bar chart data)
    - Top offending times (hourly heatmap data)
    - Detection breakdown by severity (critical, warning, info)
    - Response times (average time to acknowledge alerts)
    - System uptime (cameras, pipeline, services)
    - VLM analysis summary (confirmed vs false positive rate)
    - License plates captured (unique vehicles)
    - Evidence files generated (snapshots, video clips)

Recipients are configured via the UI at /api/reports/recipients.
Email settings (SMTP) are configured via environment variables.
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import aiosmtplib
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.db.models import (
    AlertSeverity,
    Camera,
    EvidenceFile,
    PlateRead,
    ViolationEvent,
    SystemConfig,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------


async def count_events(db: AsyncSession, days: int, offset: int = 0) -> int:
    """Count violation events in a time window."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days + offset)
    until = now - timedelta(days=offset)
    result = await db.scalar(
        select(func.count(ViolationEvent.id)).where(
            ViolationEvent.timestamp >= since,
            ViolationEvent.timestamp < until,
        )
    )
    return result or 0


async def events_by_camera(db: AsyncSession, days: int) -> list[dict]:
    """Event counts grouped by camera."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            Camera.name,
            Camera.zone,
            func.count(ViolationEvent.id).label("count"),
        )
        .join(Camera, ViolationEvent.camera_id == Camera.id)
        .where(ViolationEvent.timestamp >= since)
        .group_by(Camera.name, Camera.zone)
        .order_by(func.count(ViolationEvent.id).desc())
    )
    return [{"camera": r.name, "zone": r.zone, "count": r.count} for r in result]


async def events_by_zone(db: AsyncSession, days: int) -> list[dict]:
    """Event counts grouped by zone."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            Camera.zone,
            func.count(ViolationEvent.id).label("count"),
        )
        .join(Camera, ViolationEvent.camera_id == Camera.id)
        .where(ViolationEvent.timestamp >= since)
        .group_by(Camera.zone)
        .order_by(func.count(ViolationEvent.id).desc())
    )
    return [{"zone": r.zone, "count": r.count} for r in result]


async def hourly_distribution(db: AsyncSession, days: int) -> list[dict]:
    """Hourly event distribution (0-23)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            func.extract("hour", ViolationEvent.timestamp).label("hour"),
            func.count(ViolationEvent.id).label("count"),
        )
        .where(ViolationEvent.timestamp >= since)
        .group_by("hour")
        .order_by("hour")
    )
    return [{"hour": int(r.hour), "count": r.count} for r in result]


async def count_by_severity(
    db: AsyncSession, severity: str, days: int
) -> int:
    """Count events by severity level."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.scalar(
        select(func.count(ViolationEvent.id)).where(
            ViolationEvent.timestamp >= since,
            ViolationEvent.severity == AlertSeverity(severity),
        )
    )
    return result or 0


async def count_vlm_confirmed(db: AsyncSession, days: int) -> int:
    """Count VLM analyses that confirmed dumping (is_valid=True on linked events)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.scalar(
        select(func.count(ViolationEvent.id)).where(
            ViolationEvent.timestamp >= since,
            ViolationEvent.vlm_response.isnot(None),
            ViolationEvent.is_valid.is_(True),
        )
    )
    return result or 0


async def count_vlm_total(db: AsyncSession, days: int) -> int:
    """Count total events that went through VLM analysis."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.scalar(
        select(func.count(ViolationEvent.id)).where(
            ViolationEvent.timestamp >= since,
            ViolationEvent.vlm_response.isnot(None),
        )
    )
    return result or 0


async def unique_plates(db: AsyncSession, days: int) -> int:
    """Count unique license plates captured."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.scalar(
        select(func.count(func.distinct(PlateRead.plate_number))).where(
            PlateRead.timestamp >= since,
        )
    )
    return result or 0


async def avg_acknowledgment_time(db: AsyncSession, days: int) -> float | None:
    """Average seconds between event creation and acknowledgment."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.scalar(
        select(
            func.avg(
                func.extract(
                    "epoch",
                    ViolationEvent.acknowledged_at - ViolationEvent.timestamp,
                )
            )
        ).where(
            ViolationEvent.timestamp >= since,
            ViolationEvent.acknowledged_at.isnot(None),
        )
    )
    return round(float(result), 1) if result else None


async def count_evidence_files(db: AsyncSession, days: int) -> int:
    """Count evidence files generated in period."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.scalar(
        select(func.count(EvidenceFile.id)).where(
            EvidenceFile.created_at >= since,
        )
    )
    return result or 0


async def camera_stats(db: AsyncSession) -> dict:
    """Current camera online/total counts."""
    total = await db.scalar(select(func.count(Camera.id))) or 0
    online = await db.scalar(
        select(func.count(Camera.id)).where(Camera.status == "online")
    ) or 0
    return {"online": online, "total": total}


async def weekly_trend(db: AsyncSession, weeks: int = 4) -> list[dict]:
    """Week-over-week event counts for trending."""
    trend = []
    for i in range(weeks):
        count = await count_events(db, days=7, offset=7 * i)
        end = datetime.now(timezone.utc) - timedelta(days=7 * i)
        start = end - timedelta(days=7)
        trend.append(
            {
                "week_start": start.strftime("%Y-%m-%d"),
                "week_end": end.strftime("%Y-%m-%d"),
                "count": count,
            }
        )
    trend.reverse()
    return trend


# ---------------------------------------------------------------------------
# Report Scheduler
# ---------------------------------------------------------------------------


class ReportScheduler:
    """Scheduled report generation and email delivery service."""

    def __init__(self) -> None:
        self._engine = create_async_engine(
            settings.database_url, echo=False, pool_size=5, max_overflow=5
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

        # SMTP config from settings
        self._smtp_host: str = settings.smtp_host
        self._smtp_port: int = settings.smtp_port
        self._smtp_user: str = settings.smtp_user
        self._smtp_pass: str = settings.smtp_password
        self._smtp_from: str = settings.smtp_from
        self._smtp_tls: bool = settings.smtp_tls
        self._smtp_enabled: bool = settings.smtp_enabled

        self._running: bool = False
        self._weekly_sent_today: bool = False
        self._monthly_sent_today: bool = False

        # BitNet narrator for AI-generated report sections
        from services.reports.bitnet_narrator import BitNetNarrator
        self._narrator = BitNetNarrator()
        if self._narrator.available:
            logger.info("BitNet narrator active — reports will include AI-generated narratives")
        else:
            logger.info("BitNet narrator unavailable — using template fallbacks")

    def _get_db(self) -> AsyncSession:
        return self._session_factory()

    async def run(self) -> None:
        """Main loop — checks every hour if it's time to generate a report."""
        self._running = True
        logger.info("ReportScheduler started")

        while self._running:
            try:
                now = datetime.now(IST)

                # Weekly: Monday 8:00 AM IST
                if (
                    now.weekday() == 0
                    and now.hour == 8
                    and not self._weekly_sent_today
                ):
                    logger.info("Generating weekly report")
                    report = await self.generate_weekly_report()
                    await self.send_report(report, "weekly")
                    self._weekly_sent_today = True
                    await self._save_report_history(report, "weekly")

                # Monthly: 1st of month 8:00 AM IST
                if (
                    now.day == 1
                    and now.hour == 8
                    and not self._monthly_sent_today
                ):
                    logger.info("Generating monthly report")
                    report = await self.generate_monthly_report()
                    await self.send_report(report, "monthly")
                    self._monthly_sent_today = True
                    await self._save_report_history(report, "monthly")

                # Reset flags at midnight
                if now.hour == 0:
                    self._weekly_sent_today = False
                    self._monthly_sent_today = False

            except Exception:
                logger.exception("Error in report scheduler loop")

            await asyncio.sleep(3600)  # Check every hour

    async def stop(self) -> None:
        """Gracefully stop the scheduler."""
        self._running = False
        await self._engine.dispose()
        logger.info("ReportScheduler stopped")

    async def generate_weekly_report(self) -> dict:
        """Generate analytics data for the last 7 days."""
        now = datetime.now(IST)
        async with self._session_factory() as db:
            this_week = await count_events(db, days=7)
            last_week = await count_events(db, days=7, offset=7)
            change_pct = ((this_week - last_week) / max(last_week, 1)) * 100

            by_camera = await events_by_camera(db, days=7)
            by_zone = await events_by_zone(db, days=7)
            hourly = await hourly_distribution(db, days=7)

            critical = await count_by_severity(db, "critical", days=7)
            warning = await count_by_severity(db, "warning", days=7)
            info = await count_by_severity(db, "info", days=7)

            vlm_confirmed = await count_vlm_confirmed(db, days=7)
            vlm_total = await count_vlm_total(db, days=7)

            plates = await unique_plates(db, days=7)
            avg_resp = await avg_acknowledgment_time(db, days=7)
            evidence_count = await count_evidence_files(db, days=7)
            cams = await camera_stats(db)

        return {
            "type": "weekly",
            "period_start": (now - timedelta(days=7)).isoformat(),
            "period_end": now.isoformat(),
            "total_events": this_week,
            "prev_period_events": last_week,
            "change_pct": round(change_pct, 1),
            "by_camera": by_camera,
            "by_zone": by_zone,
            "hourly_distribution": hourly,
            "critical_count": critical,
            "warning_count": warning,
            "info_count": info,
            "vlm_confirmed": vlm_confirmed,
            "vlm_total": vlm_total,
            "vlm_false_positive_rate": round(
                1 - vlm_confirmed / max(vlm_total, 1), 2
            ),
            "unique_plates": plates,
            "avg_response_time_s": avg_resp,
            "evidence_files": evidence_count,
            "cameras": cams,
        }

        # Generate AI narrative via BitNet — generate_full_narrative() is a
        # sync CPU-bound llama_cpp call (~15-30 tok/s), multi-second per
        # report; off the event loop so it doesn't stall this service's
        # scheduler and any concurrent DB work for the duration.
        loop = asyncio.get_running_loop()
        report["narrative"] = await loop.run_in_executor(
            None, self._narrator.generate_full_narrative, report, "weekly"
        )

        return report

    async def generate_monthly_report(self) -> dict:
        """Generate analytics data for the last 30 days with week-over-week trends."""
        now = datetime.now(IST)
        async with self._session_factory() as db:
            this_month = await count_events(db, days=30)
            last_month = await count_events(db, days=30, offset=30)
            change_pct = ((this_month - last_month) / max(last_month, 1)) * 100

            by_camera = await events_by_camera(db, days=30)
            by_zone = await events_by_zone(db, days=30)
            hourly = await hourly_distribution(db, days=30)

            critical = await count_by_severity(db, "critical", days=30)
            warning = await count_by_severity(db, "warning", days=30)
            info = await count_by_severity(db, "info", days=30)

            vlm_confirmed = await count_vlm_confirmed(db, days=30)
            vlm_total = await count_vlm_total(db, days=30)

            plates = await unique_plates(db, days=30)
            avg_resp = await avg_acknowledgment_time(db, days=30)
            evidence_count = await count_evidence_files(db, days=30)
            cams = await camera_stats(db)
            trend = await weekly_trend(db, weeks=4)

        return {
            "type": "monthly",
            "period_start": (now - timedelta(days=30)).isoformat(),
            "period_end": now.isoformat(),
            "total_events": this_month,
            "prev_period_events": last_month,
            "change_pct": round(change_pct, 1),
            "by_camera": by_camera,
            "by_zone": by_zone,
            "hourly_distribution": hourly,
            "critical_count": critical,
            "warning_count": warning,
            "info_count": info,
            "vlm_confirmed": vlm_confirmed,
            "vlm_total": vlm_total,
            "vlm_false_positive_rate": round(
                1 - vlm_confirmed / max(vlm_total, 1), 2
            ),
            "unique_plates": plates,
            "avg_response_time_s": avg_resp,
            "evidence_files": evidence_count,
            "cameras": cams,
            "weekly_trend": trend,
        }

        # Generate AI narrative via BitNet
        loop = asyncio.get_running_loop()
        report["narrative"] = await loop.run_in_executor(
            None, self._narrator.generate_full_narrative, report, "monthly"
        )

        return report

    async def send_report(self, report: dict, report_type: str) -> None:
        """Send report email to all configured recipients."""
        if not self._smtp_enabled:
            logger.warning(
                "SMTP not enabled — skipping email for %s report", report_type
            )
            return

        recipients = await self._get_recipients(report_type)
        if not recipients:
            logger.warning(
                "No recipients configured for %s report", report_type
            )
            return

        html = self._render_html(report, report_type)
        period_end = report["period_end"][:10]

        msg = MIMEMultipart("alternative")
        msg["Subject"] = (
            f"Civilytics {report_type.title()} Report \u2014 {period_end}"
        )
        msg["From"] = self._smtp_from
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html, "html"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._smtp_host,
                port=self._smtp_port,
                username=self._smtp_user or None,
                password=self._smtp_pass or None,
                use_tls=self._smtp_tls,
            )
            logger.info(
                "Sent %s report to %d recipients", report_type, len(recipients)
            )
        except Exception:
            logger.exception("Failed to send %s report email", report_type)

    async def _get_recipients(self, report_type: str) -> list[str]:
        """Load email recipients from SystemConfig."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(SystemConfig).where(
                    SystemConfig.key == "report_recipients"
                )
            )
            config = result.scalar_one_or_none()

        if not config:
            return []

        recipients_data = config.value
        if isinstance(recipients_data, dict) and "items" in recipients_data:
            recipients_data = recipients_data["items"]

        if not isinstance(recipients_data, list):
            return []

        emails = []
        for r in recipients_data:
            if not r.get("enabled", True):
                continue
            if report_type in r.get("report_types", ["weekly", "monthly"]):
                emails.append(r["email"])
        return emails

    async def _save_report_history(
        self, report: dict, report_type: str
    ) -> None:
        """Persist report metadata in SystemConfig for history tracking."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(SystemConfig).where(
                    SystemConfig.key == "report_history"
                )
            )
            config = result.scalar_one_or_none()

            history: list = []
            if config and isinstance(config.value, list):
                history = config.value
            elif config and isinstance(config.value, dict):
                history = config.value.get("items", [])

            entry = {
                "type": report_type,
                "period_start": report["period_start"],
                "period_end": report["period_end"],
                "total_events": report["total_events"],
                "generated_at": datetime.now(IST).isoformat(),
                "recipients_count": len(
                    await self._get_recipients(report_type)
                ),
            }
            history.append(entry)

            # Keep last 100 entries
            history = history[-100:]

            if config:
                config.value = history
            else:
                db.add(SystemConfig(key="report_history", value=history))

            await db.commit()

    def _render_html(self, report: dict, report_type: str) -> str:
        """Build branded HTML email with Satyaki colors."""
        period_label = "Weekly" if report_type == "weekly" else "Monthly"
        period_start = report["period_start"][:10]
        period_end = report["period_end"][:10]
        prev_label = "last week" if report_type == "weekly" else "last month"

        total = report["total_events"]
        prev = report["prev_period_events"]
        change = report["change_pct"]
        change_color = "#16a34a" if change <= 0 else "#dc2626"
        change_arrow = "\u2193" if change <= 0 else "\u2191"
        change_sign = "" if change <= 0 else "+"

        critical = report["critical_count"]
        warning = report["warning_count"]
        info = report.get("info_count", 0)

        vlm_confirmed = report["vlm_confirmed"]
        vlm_total = report["vlm_total"]
        fp_rate = report["vlm_false_positive_rate"]
        fp_pct = round(fp_rate * 100, 1)

        plates = report["unique_plates"]
        avg_resp = report["avg_response_time_s"]
        avg_resp_str = f"{avg_resp}s" if avg_resp is not None else "N/A"

        evidence = report.get("evidence_files", 0)
        cams = report.get("cameras", {})
        cams_online = cams.get("online", 0)
        cams_total = cams.get("total", 0)

        # Camera table rows
        camera_rows = ""
        for i, cam in enumerate(report.get("by_camera", [])[:10]):
            bg = "#f9fafb" if i % 2 == 0 else "#ffffff"
            camera_rows += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:10px 16px;border-bottom:1px solid #e5e7eb;">{cam["camera"]}</td>'
                f'<td style="padding:10px 16px;border-bottom:1px solid #e5e7eb;">{cam["zone"]}</td>'
                f'<td style="padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:600;">{cam["count"]}</td>'
                f"</tr>"
            )

        # Zone table rows
        zone_rows = ""
        for i, z in enumerate(report.get("by_zone", [])[:10]):
            bg = "#f9fafb" if i % 2 == 0 else "#ffffff"
            zone_rows += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:10px 16px;border-bottom:1px solid #e5e7eb;">{z["zone"]}</td>'
                f'<td style="padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:600;">{z["count"]}</td>'
                f"</tr>"
            )

        # Hourly heatmap (simple bar representation)
        hourly_data = {h["hour"]: h["count"] for h in report.get("hourly_distribution", [])}
        max_hourly = max(hourly_data.values()) if hourly_data else 1
        hourly_cells = ""
        for h in range(24):
            count = hourly_data.get(h, 0)
            intensity = count / max(max_hourly, 1)
            if intensity > 0.75:
                cell_bg = "#dc2626"
                cell_fg = "#ffffff"
            elif intensity > 0.5:
                cell_bg = "#f97316"
                cell_fg = "#ffffff"
            elif intensity > 0.25:
                cell_bg = "#facc15"
                cell_fg = "#1f2937"
            elif intensity > 0:
                cell_bg = "#fef9c3"
                cell_fg = "#1f2937"
            else:
                cell_bg = "#f3f4f6"
                cell_fg = "#9ca3af"
            hourly_cells += (
                f'<td style="padding:6px 4px;text-align:center;background:{cell_bg};color:{cell_fg};'
                f'font-size:11px;border-radius:4px;min-width:28px;"'
                f' title="{count} events at {h:02d}:00">{count}</td>'
            )

        # Weekly trend rows (monthly report only)
        trend_section = ""
        if report_type == "monthly" and "weekly_trend" in report:
            trend_rows = ""
            for i, w in enumerate(report["weekly_trend"]):
                bg = "#f9fafb" if i % 2 == 0 else "#ffffff"
                trend_rows += (
                    f'<tr style="background:{bg};">'
                    f'<td style="padding:10px 16px;border-bottom:1px solid #e5e7eb;">'
                    f'{w["week_start"]} to {w["week_end"]}</td>'
                    f'<td style="padding:10px 16px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:600;">'
                    f'{w["count"]}</td>'
                    f"</tr>"
                )
            trend_section = f"""
            <div style="margin-top:32px;">
                <h2 style="color:#1f2937;font-family:'Poppins',Helvetica,Arial,sans-serif;font-size:18px;
                    font-weight:600;margin:0 0 16px 0;">Weekly Trend</h2>
                <table style="width:100%;border-collapse:collapse;border-radius:8px;overflow:hidden;
                    box-shadow:0 1px 3px rgba(0,0,0,0.08);">
                    <thead>
                        <tr style="background:#1f2937;">
                            <th style="padding:12px 16px;text-align:left;color:#ffffff;font-weight:600;
                                font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">Period</th>
                            <th style="padding:12px 16px;text-align:right;color:#ffffff;font-weight:600;
                                font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">Events</th>
                        </tr>
                    </thead>
                    <tbody>{trend_rows}</tbody>
                </table>
            </div>
            """

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Civilytics {period_label} Report</title>
    <!--[if mso]>
    <style type="text/css">
        table, td {{font-family: Arial, sans-serif;}}
    </style>
    <![endif]-->
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:'Poppins',Helvetica,Arial,sans-serif;">

<!-- Wrapper -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
<tr><td align="center" style="padding:24px 16px;">

<!-- Container -->
<table role="presentation" width="640" cellpadding="0" cellspacing="0"
    style="max-width:640px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;
    box-shadow:0 4px 24px rgba(0,0,0,0.08);">

<!-- Header -->
<tr>
    <td style="background:#101010;padding:32px 24px;text-align:center;">
        <h1 style="margin:0 0 4px 0;color:#FCA719;font-family:'Poppins',Helvetica,Arial,sans-serif;
            font-size:28px;font-weight:700;letter-spacing:-0.5px;">Civilytics</h1>
        <p style="margin:0 0 4px 0;color:#ffffff;font-family:'Poppins',Helvetica,Arial,sans-serif;
            font-size:16px;font-weight:500;">{period_label} Surveillance Report</p>
        <p style="margin:0;color:#9ca3af;font-size:13px;">
            Lake Shore Gardens, Vidyaranyapura, Bangalore</p>
        <p style="margin:8px 0 0 0;color:#6b7280;font-size:12px;">
            {period_start} to {period_end}</p>
    </td>
</tr>

<!-- Key Metrics -->
<tr>
    <td style="padding:24px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr>
            <!-- Total Violations -->
            <td width="33%" style="padding:0 6px 0 0;">
                <div style="background:#fef2f2;border-left:4px solid #ef4444;border-radius:8px;
                    padding:16px;">
                    <p style="margin:0;color:#991b1b;font-size:28px;font-weight:700;
                        font-family:'Poppins',Helvetica,Arial,sans-serif;">{total}</p>
                    <p style="margin:4px 0 0 0;color:#7f1d1d;font-size:12px;font-weight:600;
                        text-transform:uppercase;letter-spacing:0.5px;">Total Violations</p>
                    <p style="margin:6px 0 0 0;color:{change_color};font-size:13px;font-weight:600;">
                        {change_arrow} {change_sign}{change}% vs {prev_label}</p>
                </div>
            </td>
            <!-- Critical -->
            <td width="33%" style="padding:0 3px;">
                <div style="background:#fff7ed;border-left:4px solid #f97316;border-radius:8px;
                    padding:16px;">
                    <p style="margin:0;color:#9a3412;font-size:28px;font-weight:700;
                        font-family:'Poppins',Helvetica,Arial,sans-serif;">{critical}</p>
                    <p style="margin:4px 0 0 0;color:#7c2d12;font-size:12px;font-weight:600;
                        text-transform:uppercase;letter-spacing:0.5px;">Critical</p>
                    <p style="margin:6px 0 0 0;color:#6b7280;font-size:13px;">
                        {warning} warning, {info} info</p>
                </div>
            </td>
            <!-- Response Time -->
            <td width="33%" style="padding:0 0 0 6px;">
                <div style="background:#eff6ff;border-left:4px solid #458DFF;border-radius:8px;
                    padding:16px;">
                    <p style="margin:0;color:#1e40af;font-size:28px;font-weight:700;
                        font-family:'Poppins',Helvetica,Arial,sans-serif;">{avg_resp_str}</p>
                    <p style="margin:4px 0 0 0;color:#1e3a5f;font-size:12px;font-weight:600;
                        text-transform:uppercase;letter-spacing:0.5px;">Avg Response</p>
                    <p style="margin:6px 0 0 0;color:#6b7280;font-size:13px;">
                        acknowledgment time</p>
                </div>
            </td>
        </tr>
        </table>
    </td>
</tr>

<!-- Secondary Metrics Row -->
<tr>
    <td style="padding:0 24px 24px 24px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr>
            <!-- VLM Accuracy -->
            <td width="25%" style="padding:0 6px 0 0;">
                <div style="background:#f0fdf4;border-radius:8px;padding:14px;text-align:center;">
                    <p style="margin:0;color:#166534;font-size:22px;font-weight:700;
                        font-family:'Poppins',Helvetica,Arial,sans-serif;">{vlm_confirmed}/{vlm_total}</p>
                    <p style="margin:4px 0 0 0;color:#14532d;font-size:11px;font-weight:600;
                        text-transform:uppercase;">VLM Confirmed</p>
                    <p style="margin:2px 0 0 0;color:#6b7280;font-size:11px;">{fp_pct}% false pos.</p>
                </div>
            </td>
            <!-- Plates -->
            <td width="25%" style="padding:0 3px;">
                <div style="background:#faf5ff;border-radius:8px;padding:14px;text-align:center;">
                    <p style="margin:0;color:#6b21a8;font-size:22px;font-weight:700;
                        font-family:'Poppins',Helvetica,Arial,sans-serif;">{plates}</p>
                    <p style="margin:4px 0 0 0;color:#581c87;font-size:11px;font-weight:600;
                        text-transform:uppercase;">Plates Captured</p>
                    <p style="margin:2px 0 0 0;color:#6b7280;font-size:11px;">unique vehicles</p>
                </div>
            </td>
            <!-- Evidence -->
            <td width="25%" style="padding:0 3px;">
                <div style="background:#fefce8;border-radius:8px;padding:14px;text-align:center;">
                    <p style="margin:0;color:#854d0e;font-size:22px;font-weight:700;
                        font-family:'Poppins',Helvetica,Arial,sans-serif;">{evidence}</p>
                    <p style="margin:4px 0 0 0;color:#713f12;font-size:11px;font-weight:600;
                        text-transform:uppercase;">Evidence Files</p>
                    <p style="margin:2px 0 0 0;color:#6b7280;font-size:11px;">snapshots + clips</p>
                </div>
            </td>
            <!-- Cameras -->
            <td width="25%" style="padding:0 0 0 6px;">
                <div style="background:#f0f9ff;border-radius:8px;padding:14px;text-align:center;">
                    <p style="margin:0;color:#075985;font-size:22px;font-weight:700;
                        font-family:'Poppins',Helvetica,Arial,sans-serif;">{cams_online}/{cams_total}</p>
                    <p style="margin:4px 0 0 0;color:#0c4a6e;font-size:11px;font-weight:600;
                        text-transform:uppercase;">Cameras Online</p>
                    <p style="margin:2px 0 0 0;color:#6b7280;font-size:11px;">currently active</p>
                </div>
            </td>
        </tr>
        </table>
    </td>
</tr>

<!-- Violations by Camera -->
<tr>
    <td style="padding:0 24px 24px 24px;">
        <h2 style="color:#1f2937;font-family:'Poppins',Helvetica,Arial,sans-serif;font-size:18px;
            font-weight:600;margin:0 0 16px 0;">Violations by Camera</h2>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
            style="border-collapse:collapse;border-radius:8px;overflow:hidden;
            box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <thead>
                <tr style="background:#1f2937;">
                    <th style="padding:12px 16px;text-align:left;color:#ffffff;font-weight:600;
                        font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">Camera</th>
                    <th style="padding:12px 16px;text-align:left;color:#ffffff;font-weight:600;
                        font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">Zone</th>
                    <th style="padding:12px 16px;text-align:right;color:#ffffff;font-weight:600;
                        font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">Events</th>
                </tr>
            </thead>
            <tbody>{camera_rows if camera_rows else '<tr><td colspan="3" style="padding:16px;text-align:center;color:#9ca3af;">No camera data</td></tr>'}</tbody>
        </table>
    </td>
</tr>

<!-- Violations by Zone -->
<tr>
    <td style="padding:0 24px 24px 24px;">
        <h2 style="color:#1f2937;font-family:'Poppins',Helvetica,Arial,sans-serif;font-size:18px;
            font-weight:600;margin:0 0 16px 0;">Violations by Zone</h2>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
            style="border-collapse:collapse;border-radius:8px;overflow:hidden;
            box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <thead>
                <tr style="background:#1f2937;">
                    <th style="padding:12px 16px;text-align:left;color:#ffffff;font-weight:600;
                        font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">Zone</th>
                    <th style="padding:12px 16px;text-align:right;color:#ffffff;font-weight:600;
                        font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">Events</th>
                </tr>
            </thead>
            <tbody>{zone_rows if zone_rows else '<tr><td colspan="2" style="padding:16px;text-align:center;color:#9ca3af;">No zone data</td></tr>'}</tbody>
        </table>
    </td>
</tr>

<!-- Hourly Heatmap -->
<tr>
    <td style="padding:0 24px 24px 24px;">
        <h2 style="color:#1f2937;font-family:'Poppins',Helvetica,Arial,sans-serif;font-size:18px;
            font-weight:600;margin:0 0 16px 0;">Hourly Activity Heatmap</h2>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="2"
            style="border-collapse:separate;">
            <tr>
                {''.join(f'<td style="padding:4px 2px;text-align:center;color:#9ca3af;font-size:10px;">{h:02d}</td>' for h in range(24))}
            </tr>
            <tr>{hourly_cells}</tr>
        </table>
        <p style="margin:8px 0 0 0;color:#9ca3af;font-size:11px;">
            Hours shown in UTC. Hover for event counts.</p>
    </td>
</tr>

<!-- Weekly Trend (monthly only) -->
{f'<tr><td style="padding:0 24px 24px 24px;">{trend_section}</td></tr>' if trend_section else ''}

<!-- AI-Generated Narrative (BitNet) -->
{self._render_narrative_html(report)}

<!-- Footer -->
<tr>
    <td style="background:#101010;padding:24px;text-align:center;">
        <p style="margin:0 0 8px 0;color:#FCA719;font-family:'Poppins',Helvetica,Arial,sans-serif;
            font-size:14px;font-weight:600;">Powered by Satyaki Solutions</p>
        <p style="margin:0 0 4px 0;color:#6b7280;font-size:12px;">
            Civilytics AI Surveillance Platform</p>
        <p style="margin:0 0 12px 0;color:#6b7280;font-size:12px;">
            Lake Shore Gardens, Vidyaranyapura, Bangalore 560097</p>
        <div style="border-top:1px solid #374151;padding-top:12px;margin-top:4px;">
            <p style="margin:0;color:#4b5563;font-size:11px;">
                This is an automated report. To change recipients or unsubscribe,
                visit the Civilytics dashboard settings.</p>
            <p style="margin:8px 0 0 0;color:#374151;font-size:11px;">
                &copy; 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.</p>
        </div>
    </td>
</tr>

</table>
<!-- /Container -->

</td></tr>
</table>
<!-- /Wrapper -->

</body>
</html>"""
        return html

    def _render_narrative_html(self, report: dict) -> str:
        """Render the AI-generated narrative sections as HTML."""
        narrative = report.get("narrative", {})
        if not narrative or narrative.get("generated_by") == "template" and not narrative.get("executive_summary"):
            return ""

        def _section(title: str, key: str, icon: str) -> str:
            text = narrative.get(key, "")
            if not text:
                return ""
            # Convert newlines to <br> for HTML
            html_text = text.replace("\n", "<br>")
            return f"""
            <div style="margin-bottom:20px;">
                <h3 style="color:#1f2937;font-family:'Poppins',Helvetica,Arial,sans-serif;
                    font-size:15px;font-weight:600;margin:0 0 8px 0;">
                    {icon} {title}</h3>
                <p style="color:#374151;font-size:14px;line-height:1.6;margin:0;">
                    {html_text}</p>
            </div>"""

        engine = narrative.get("generated_by", "template")
        badge_color = "#458DFF" if engine == "BitNet-b1.58-2B-4T" else "#6b7280"
        badge_text = "AI Generated" if "BitNet" in engine else "Template"

        sections = "".join(filter(None, [
            _section("Executive Summary", "executive_summary", "&#128202;"),
            _section("Camera Analysis", "camera_analysis", "&#128247;"),
            _section("Zone Insights", "zone_insights", "&#128205;"),
            _section("Trend Analysis", "trend_analysis", "&#128200;"),
            _section("Recommendations", "recommendations", "&#128161;"),
            _section("Anomalies Detected", "anomalies", "&#9888;"),
        ]))

        if not sections:
            return ""

        return f"""
<tr>
    <td style="padding:0 24px 24px 24px;">
        <div style="margin-top:32px;border-top:2px solid #FCA719;padding-top:24px;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
                <h2 style="color:#1f2937;font-family:'Poppins',Helvetica,Arial,sans-serif;
                    font-size:18px;font-weight:600;margin:0;">Analysis &amp; Recommendations</h2>
                <span style="background:{badge_color};color:#fff;font-size:10px;font-weight:600;
                    padding:2px 8px;border-radius:4px;text-transform:uppercase;
                    letter-spacing:0.5px;">{badge_text}</span>
            </div>
            {sections}
            <p style="color:#9ca3af;font-size:10px;margin:16px 0 0 0;font-style:italic;">
                Narrative generated by {engine}. Review all recommendations before acting.</p>
        </div>
    </td>
</tr>"""


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scheduler = ReportScheduler()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(scheduler.stop()))

    await scheduler.run()


if __name__ == "__main__":
    asyncio.run(main())
