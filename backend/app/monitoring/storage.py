"""Disk and evidence storage monitoring."""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

EVIDENCE_DIR = Path("/tmp/wastewatch/evidence")


async def get_storage_stats() -> dict:
    """Get disk usage and evidence storage stats."""
    try:
        stat = os.statvfs("/")
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bfree * stat.f_frsize
        used = total - free

        evidence_size = 0
        evidence_files = 0
        if EVIDENCE_DIR.exists():
            for f in EVIDENCE_DIR.rglob("*"):
                if f.is_file():
                    evidence_size += f.stat().st_size
                    evidence_files += 1

        return {
            "disk_total_gb": round(total / (1024**3), 1),
            "disk_used_gb": round(used / (1024**3), 1),
            "disk_free_gb": round(free / (1024**3), 1),
            "disk_usage_pct": round(used / total * 100, 1) if total > 0 else 0,
            "evidence_size_mb": round(evidence_size / (1024**2), 1),
            "evidence_files": evidence_files,
        }
    except Exception as e:
        return {"error": str(e)}
