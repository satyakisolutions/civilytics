"""Model degradation level manager.

Levels:
  0 = Full (all models active)
  1 = Reduced (VLM disabled, heuristic-only alerts)
  2 = Minimal (VLM + LPR disabled, detection + tracking only)
  3 = Passthrough (all AI disabled, stream-only)
"""
import logging

logger = logging.getLogger(__name__)


class DegradeManager:
    def __init__(self):
        self._level = 0
        self._reason = "normal"
        self._auto_degrade_enabled = True

    @property
    def level(self) -> int:
        return self._level

    @property
    def reason(self) -> str:
        return self._reason

    def set_level(self, level: int, reason: str = "manual"):
        if level < 0 or level > 3:
            raise ValueError(f"Invalid degrade level: {level}")
        old = self._level
        self._level = level
        self._reason = reason
        logger.warning("Degrade level changed: %d → %d (reason: %s)", old, level, reason)

    def is_vlm_enabled(self) -> bool:
        return self._level < 1

    def is_lpr_enabled(self) -> bool:
        return self._level < 2

    def is_detection_enabled(self) -> bool:
        return self._level < 3

    def get_status(self) -> dict:
        level_names = {0: "Full", 1: "Reduced", 2: "Minimal", 3: "Passthrough"}
        return {
            "level": self._level,
            "name": level_names.get(self._level, "Unknown"),
            "reason": self._reason,
            "auto_degrade_enabled": self._auto_degrade_enabled,
            "vlm_enabled": self.is_vlm_enabled(),
            "lpr_enabled": self.is_lpr_enabled(),
            "detection_enabled": self.is_detection_enabled(),
        }

    def check_auto_degrade(self, gpu_temp: float | None = None, vlm_latency_ms: float | None = None):
        """Check auto-degrade triggers."""
        if not self._auto_degrade_enabled:
            return
        if gpu_temp and gpu_temp > 85 and self._level < 1:
            self.set_level(1, f"auto: GPU temp {gpu_temp}°C > 85°C")
        if vlm_latency_ms and vlm_latency_ms > 10000 and self._level < 1:
            self.set_level(1, f"auto: VLM latency {vlm_latency_ms}ms > 10s")


# Module-level singleton
degrade_manager = DegradeManager()
