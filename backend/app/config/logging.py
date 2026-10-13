# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Central logging setup + runtime level control.

One console handler, one format, sane defaults. App code uses the standard
levels: DEBUG (dev detail), INFO (lifecycle), WARNING (recoverable), ERROR
(failed operation, pair with exc_info), CRITICAL (process can't continue).

The level is settable at runtime via PUT /api/system/log-level with no
restart -- useful for turning on DEBUG against a live incident, then back.
Runtime changes are NOT persisted; a restart returns to settings.log_level.
"""
import logging
import logging.config

_VALID = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}

# App logger namespaces we expose/track for per-logger overrides. "" is root.
APP_LOGGERS = ("app", "uvicorn", "uvicorn.access", "sqlalchemy.engine", "aiokafka")

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Install the console handler + levels. Idempotent (call once at boot)."""
    level = _normalize(level)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,  # keep uvicorn's loggers alive
            "formatters": {"std": {"format": _FORMAT}},
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "std"},
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                # Our code at the configured level, noisy libs kept quieter.
                "app": {"level": level, "handlers": [], "propagate": True},
                "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
                "aiokafka": {"level": "WARNING", "propagate": True},
                "uvicorn.access": {"level": "INFO", "propagate": True},
            },
        }
    )


def _normalize(level: str) -> str:
    up = (level or "").strip().upper()
    if up not in _VALID:
        raise ValueError(f"invalid log level {level!r}; use one of {sorted(_VALID)}")
    return up


def set_level(level: str, logger_name: str | None = None) -> str:
    """Set a logger's level at runtime. None/''/'root' targets the root logger.

    Returns the normalized level applied.
    """
    lvl = _normalize(level)
    name = (logger_name or "").strip()
    target = logging.getLogger() if name in ("", "root") else logging.getLogger(name)
    target.setLevel(lvl)
    return lvl


def get_levels() -> dict:
    """Current effective levels: the root plus each tracked app logger."""
    root = logging.getLogger()
    out = {"root": logging.getLevelName(root.getEffectiveLevel())}
    for name in APP_LOGGERS:
        lg = logging.getLogger(name)
        # effective = what messages actually pass; set = explicit override (if any)
        out[name] = {
            "effective": logging.getLevelName(lg.getEffectiveLevel()),
            "set": logging.getLevelName(lg.level) if lg.level else "NOTSET",
        }
    return out
