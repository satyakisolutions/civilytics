# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Periodic ROI hydration from the FastAPI backend.

The pipeline's `ROIManager` starts empty. Without a sync loop, every
detection's `in_roi` flag is False forever and the FSM treats the
entire frame as "outside the monitored zone" — i.e. the operator can
draw polygons in the UI but the pipeline never actually restricts
detections to those zones.

This module runs a background daemon thread that:
  1. Fetches `/api/cameras` to learn which cameras exist.
  2. For each camera, fetches `/api/cameras/{id}/roi` to pull its
     polygons (the same shape `useROIEditor.savePolygons` writes).
  3. Maps the camera's `name` to the pipeline's `cam-XX` source key
     and calls `ROIManager.set_polygons(...)`.

Polls every `interval_s` seconds (default 10). All errors are logged
and swallowed — a transient backend hiccup must not kill the
inference pipeline.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

import httpx

from app.roi import ROIManager, ROIPolygon

logger = logging.getLogger(__name__)

# nvdsanalytics config path — written dynamically when ROIs change.
# Use /tmp so we always have write permission inside the container.
_ANALYTICS_CONFIG_PATH = Path(os.environ.get(
    "WW_ANALYTICS_CONFIG", "/tmp/nvdsanalytics_config.txt"
))


class ROISyncer:
    def __init__(
        self,
        roi_manager: ROIManager,
        api_base: str = "http://api:8080",
        interval_s: float = 10.0,
        request_timeout_s: float = 4.0,
        nvdsanalytics_elem: Optional[Gst.Element] = None,
        source_manager=None,
        config_width: int = 1920,
        config_height: int = 1080,
    ) -> None:
        self._roi_manager = roi_manager
        self._api_base = api_base.rstrip("/")
        self._interval_s = interval_s
        self._timeout_s = request_timeout_s
        self._nvdsanalytics = nvdsanalytics_elem
        self._source_manager = source_manager
        self._config_width = config_width
        self._config_height = config_height
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Track the last polygon hash per camera so we only log on change.
        self._last_signature: dict[str, str] = {}
        # Track the last written config hash to avoid unnecessary rewrites
        self._last_config_hash: str = ""
        # Per-camera detection_config (dwell/proximity threshold overrides),
        # keyed by cam_name -- populated in _sync_once from the same
        # /api/cameras response already fetched for ROI polygons, so
        # get_detection_config() never needs a separate request.
        self._detection_configs: dict[str, dict] = {}

    def get_detection_config(self, cam_name: str) -> dict:
        """Per-camera detection_config overrides (dwell/proximity
        thresholds), or {} if this camera has none configured."""
        return self._detection_configs.get(cam_name, {})

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="roi-syncer", daemon=True
        )
        self._thread.start()
        logger.info(
            "ROISyncer started — polling %s every %.1fs",
            self._api_base, self._interval_s,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 1)
            self._thread = None

    def _run(self) -> None:
        # First sync runs immediately so the pipeline doesn't spend the
        # first `interval_s` window without any ROI gating at all.
        while not self._stop.is_set():
            try:
                self._sync_once()
            except Exception:  # pragma: no cover — daemon must not die
                logger.exception("ROISyncer iteration failed")
            self._stop.wait(self._interval_s)

    def _sync_once(self) -> None:
        with httpx.Client(timeout=self._timeout_s) as client:
            try:
                resp = client.get(f"{self._api_base}/api/cameras")
                resp.raise_for_status()
            except Exception as exc:
                logger.debug("ROISyncer: GET /api/cameras failed: %s", exc)
                return
            cameras = resp.json() or []

            for cam in cameras:
                cam_id = cam.get("id")
                cam_name = cam.get("name")  # display name only, for logging
                if not cam_id or not cam_name:
                    continue
                # Everything downstream (pipeline.py's extract_metadata /
                # decorate_for_osd, source_manager) keys camera state by
                # the DB UUID (cam_id), not the display name -- keying on
                # cam_name here makes roi_manager.is_in_any_roi and
                # get_detection_config silently miss every lookup (in_roi
                # always False, detection_config always {}).
                self._detection_configs[cam_id] = cam.get("detection_config") or {}
                try:
                    roi_resp = client.get(
                        f"{self._api_base}/api/cameras/{cam_id}/roi"
                    )
                    roi_resp.raise_for_status()
                except Exception as exc:
                    logger.debug(
                        "ROISyncer: GET roi for %s failed: %s", cam_name, exc
                    )
                    continue

                payload = roi_resp.json() or {}
                polygons_raw = payload.get("polygons") or []
                polygons = [
                    ROIPolygon(
                        camera_id=cam_id,
                        name=p.get("name") or "unnamed",
                        points=[
                            (float(pt["x"]), float(pt["y"]))
                            for pt in p.get("points", [])
                            if isinstance(pt, dict) and "x" in pt and "y" in pt
                        ],
                        source_id=0,
                    )
                    for p in polygons_raw
                    if isinstance(p, dict) and p.get("points")
                ]

                signature = repr([(p.name, p.points) for p in polygons])
                if self._last_signature.get(cam_id) == signature:
                    continue  # No change; skip the noisy log.
                self._last_signature[cam_id] = signature

                self._roi_manager.set_polygons(cam_id, polygons)
                logger.info(
                    "ROISyncer: hydrated %d polygon(s) for %s (%s)",
                    len(polygons), cam_name, cam_id,
                )

            # After syncing all cameras, regenerate nvdsanalytics config
            self._write_nvdsanalytics_config()

    def _get_source_id_for_camera(self, cam_name: str) -> int | None:
        """Look up the streammux pad index for a camera name."""
        if self._source_manager is None:
            return None
        sources = getattr(self._source_manager, '_sources', {})
        entry = sources.get(cam_name)
        if entry is None:
            return None
        return entry[1]  # (uri, pad_id)

    def _write_nvdsanalytics_config(self) -> None:
        """Write nvdsanalytics config with ROI zones from all cameras.

        Generates a config file like:
            [property]
            enable=1
            config-width=1920
            config-height=1080

            [roi-filtering-stream-0]
            enable=1
            class-id=-1
            roi-RestrictedZone=100;200;300;200;300;400;100;400
        """
        lines = [
            "[property]",
            "enable=1",
            f"config-width={self._config_width}",
            f"config-height={self._config_height}",
            "",
        ]

        all_polygons = self._roi_manager.get_all_polygons()
        has_roi = False

        for cam_name, polys in all_polygons.items():
            if not polys:
                continue
            source_id = self._get_source_id_for_camera(cam_name)
            if source_id is None:
                # Fallback: parse cam-XX → XX as source_id
                try:
                    source_id = int(cam_name.split("-")[-1])
                except (ValueError, IndexError):
                    continue

            lines.append(f"[roi-filtering-stream-{source_id}]")
            lines.append("enable=1")
            lines.append("class-id=-1")

            for poly in polys:
                # Convert normalized (0-1) coords to pixel coords
                label = poly.name.replace(" ", "").replace("-", "_")
                pixel_points = []
                for x_norm, y_norm in poly.points:
                    px = int(x_norm * self._config_width)
                    py = int(y_norm * self._config_height)
                    pixel_points.extend([str(px), str(py)])
                roi_str = ";".join(pixel_points)
                lines.append(f"roi-{label}={roi_str}")
                has_roi = True

            lines.append("")

        config_text = "\n".join(lines)
        config_hash = repr(config_text)

        if config_hash == self._last_config_hash:
            return  # No change

        self._last_config_hash = config_hash

        try:
            _ANALYTICS_CONFIG_PATH.write_text(config_text)
            logger.info(
                "nvdsanalytics config updated: %s (%d ROI zones)",
                _ANALYTICS_CONFIG_PATH,
                sum(len(p) for p in all_polygons.values()),
            )
        except OSError:
            logger.exception("Failed to write nvdsanalytics config")
            return

        # Reload nvdsanalytics by re-setting the config-file property
        if self._nvdsanalytics is not None:
            try:
                self._nvdsanalytics.set_property(
                    "config-file", str(_ANALYTICS_CONFIG_PATH)
                )
                logger.info("nvdsanalytics config-file property updated — ROI reload triggered")
            except Exception:
                logger.exception("Failed to reload nvdsanalytics config")
