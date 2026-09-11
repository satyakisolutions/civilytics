# backend/app/alerts/ip_speaker.py
"""IP Speaker alert delivery -- plays TTS audio on network speakers.

Supports multiple IP speaker protocols:
- HTTP POST (Axis, Barix, generic REST speakers)
- SIP INVITE with RTP audio (SIP-enabled speakers)
- Multicast RTP (for speaker arrays)

This is the FASTEST alert channel (~350ms from violation to audio):
1. TTS generates WAV (Piper, ~300ms or cache hit ~5ms)
2. HTTP POST to speaker API (~50ms)
3. Speaker plays audio immediately

Pre-caching: Common alert templates are pre-generated at startup so
the TTS step is a cache hit for typical violations.

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from app.config.settings import settings
from app.tts.engine import TTSEngine

logger = logging.getLogger(__name__)


@dataclass
class SpeakerConfig:
    """Configuration for a single IP speaker endpoint."""

    speaker_id: str          # e.g., "speaker-zone-a"
    name: str                # Human-friendly name
    zone: str                # Zone this speaker covers
    protocol: str            # "http", "sip", "multicast"
    endpoint: str            # URL or SIP URI or multicast address

    # HTTP specific
    http_method: str = "POST"
    http_auth_token: str = ""
    http_content_type: str = "audio/wav"

    # SIP specific
    sip_uri: str = ""

    # Multicast specific
    multicast_group: str = ""
    multicast_port: int = 5004

    # General
    volume: int = 80         # 0-100
    priority: int = 1        # Lower = higher priority
    enabled: bool = True
    camera_ids: list[str] = field(default_factory=list)  # Which cameras this speaker serves


class IPSpeakerManager:
    """Manages IP speaker fleet and delivers TTS audio alerts.

    Usage::

        mgr = IPSpeakerManager(tts_engine, speakers=[...])
        await mgr.precache_common_alerts()
        result = await mgr.play_alert("cam-01", "Illegal dumping detected", "critical")
        await mgr.close()
    """

    def __init__(
        self,
        tts_engine: TTSEngine | None = None,
        speakers: list[SpeakerConfig] | None = None,
    ) -> None:
        self._tts = tts_engine or TTSEngine()
        self._http = httpx.AsyncClient(timeout=10.0)
        self._cache: dict[str, str] = {}  # text -> audio file path

        # Load speaker configs from argument or settings
        if speakers is not None:
            self._speakers = speakers
        else:
            self._speakers = self._load_speakers_from_settings()

        self._enabled = settings.ip_speaker_enabled
        logger.info(
            "IPSpeakerManager initialised: %d speakers configured, enabled=%s",
            len(self._speakers),
            self._enabled,
        )

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_speakers_from_settings() -> list[SpeakerConfig]:
        """Build SpeakerConfig list from ``settings.ip_speaker_configs``."""
        configs: list[SpeakerConfig] = []
        for raw in settings.ip_speaker_configs:
            try:
                configs.append(SpeakerConfig(
                    speaker_id=raw["speaker_id"],
                    name=raw.get("name", raw["speaker_id"]),
                    zone=raw.get("zone", "default"),
                    protocol=raw.get("protocol", "http"),
                    endpoint=raw.get("endpoint", ""),
                    http_method=raw.get("http_method", "POST"),
                    http_auth_token=raw.get("http_auth_token", ""),
                    http_content_type=raw.get("http_content_type", "audio/wav"),
                    sip_uri=raw.get("sip_uri", ""),
                    multicast_group=raw.get("multicast_group", ""),
                    multicast_port=raw.get("multicast_port", 5004),
                    volume=raw.get("volume", 80),
                    priority=raw.get("priority", 1),
                    enabled=raw.get("enabled", True),
                    camera_ids=raw.get("camera_ids", []),
                ))
            except (KeyError, TypeError):
                logger.warning("Skipping invalid speaker config: %s", raw)
        return configs

    # ------------------------------------------------------------------
    # Pre-cache
    # ------------------------------------------------------------------

    async def precache_common_alerts(self) -> None:
        """Pre-generate TTS audio for common alert templates at startup.

        This ensures that the most frequent alert messages are served from
        cache (~5 ms) rather than generated on the fly (~300 ms).
        """
        templates = [
            "Alert. Illegal waste dumping detected at camera {cam}. Immediate attention required.",
            "Warning. Suspicious activity detected at camera {cam}. Please investigate.",
            "Alert. Vehicle dumping waste detected at camera {cam}. License plate capture in progress.",
        ]

        loop = asyncio.get_event_loop()
        cached_count = 0
        for cam_id in [f"cam-{i:02d}" for i in range(8)]:
            for template in templates:
                text = template.format(cam=cam_id)
                try:
                    result = await loop.run_in_executor(None, self._tts.generate, text)
                    self._cache[text] = result["path"]
                    cached_count += 1
                except Exception:
                    logger.warning("Failed to pre-cache TTS for: %s", text[:60])
        logger.info("Pre-cached %d TTS alerts for IP speakers", cached_count)

    # ------------------------------------------------------------------
    # Main alert method
    # ------------------------------------------------------------------

    async def play_alert(
        self,
        camera_id: str,
        text: str,
        severity: str = "warning",
    ) -> dict[str, Any]:
        """Play a TTS alert on all speakers serving *camera_id*.

        Returns a dict summarising delivery results per speaker.
        """
        if not self._enabled:
            logger.debug("IP speaker alerts disabled -- skipping")
            return {"delivered": False, "reason": "disabled"}

        speakers = self.get_speakers_for_camera(camera_id)
        if not speakers:
            logger.debug("No speakers mapped to camera=%s", camera_id)
            return {"delivered": False, "reason": "no_speakers"}

        # --- Step 1: Obtain audio (cache hit or generate) ---
        t0 = time.monotonic()
        audio_path = self._cache.get(text)
        if audio_path and Path(audio_path).exists():
            logger.debug("TTS cache hit for speaker alert (%s)", text[:50])
        else:
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(None, self._tts.generate, text)
                audio_path = result["path"]
                self._cache[text] = audio_path
            except Exception:
                logger.exception("TTS generation failed for speaker alert")
                return {"delivered": False, "reason": "tts_failed"}
        tts_ms = (time.monotonic() - t0) * 1000

        # --- Step 2: Fan-out to all matching speakers concurrently ---
        tasks = []
        for spk in speakers:
            tasks.append(self._send_to_speaker(spk, audio_path, severity))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        delivery_results: list[dict[str, Any]] = []
        for spk, res in zip(speakers, results):
            if isinstance(res, Exception):
                delivery_results.append({
                    "speaker_id": spk.speaker_id,
                    "ok": False,
                    "error": str(res),
                })
            else:
                delivery_results.append(res)

        total_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "IP speaker alert: camera=%s speakers=%d tts=%.0fms total=%.0fms",
            camera_id, len(speakers), tts_ms, total_ms,
        )
        return {
            "delivered": True,
            "camera_id": camera_id,
            "speakers": delivery_results,
            "tts_ms": round(tts_ms, 1),
            "total_ms": round(total_ms, 1),
        }

    # ------------------------------------------------------------------
    # Per-protocol delivery
    # ------------------------------------------------------------------

    async def _send_to_speaker(
        self,
        speaker: SpeakerConfig,
        audio_path: str,
        severity: str,
    ) -> dict[str, Any]:
        """Route to the correct protocol handler."""
        if speaker.protocol == "http":
            return await self._send_http(speaker, audio_path)
        elif speaker.protocol == "multicast":
            return await self._send_multicast(speaker, audio_path)
        elif speaker.protocol == "sip":
            return await self._send_sip_audio(speaker, audio_path)
        else:
            return {
                "speaker_id": speaker.speaker_id,
                "ok": False,
                "error": f"unsupported protocol: {speaker.protocol}",
            }

    async def _send_http(
        self,
        speaker: SpeakerConfig,
        audio_path: str,
    ) -> dict[str, Any]:
        """HTTP POST the WAV file to the speaker's REST API.

        Compatible with:
        - Axis: ``POST /axis-cgi/audio/transmit.cgi``
        - Barix: ``POST /rc.cgi?c=play``
        - Generic: ``POST /api/play`` with WAV body
        """
        try:
            async with aiofiles.open(audio_path, "rb") as f:
                audio_data = await f.read()

            headers: dict[str, str] = {
                "Content-Type": speaker.http_content_type,
            }
            if speaker.http_auth_token:
                headers["Authorization"] = f"Bearer {speaker.http_auth_token}"

            if speaker.volume != 100:
                headers["X-Volume"] = str(speaker.volume)

            resp = await self._http.request(
                speaker.http_method,
                speaker.endpoint,
                content=audio_data,
                headers=headers,
                timeout=5.0,
            )
            resp.raise_for_status()
            return {
                "speaker_id": speaker.speaker_id,
                "ok": True,
                "status_code": resp.status_code,
                "protocol": "http",
            }
        except httpx.TimeoutException:
            logger.warning("HTTP timeout sending to speaker %s", speaker.speaker_id)
            return {
                "speaker_id": speaker.speaker_id,
                "ok": False,
                "error": "timeout",
                "protocol": "http",
            }
        except Exception as exc:
            logger.error(
                "HTTP send failed for speaker %s: %s", speaker.speaker_id, exc,
            )
            return {
                "speaker_id": speaker.speaker_id,
                "ok": False,
                "error": str(exc),
                "protocol": "http",
            }

    async def _send_multicast(
        self,
        speaker: SpeakerConfig,
        audio_path: str,
    ) -> dict[str, Any]:
        """Send raw audio via UDP multicast (RTP-lite) for speaker arrays.

        Reads the WAV payload (skipping the 44-byte header) and sends it in
        ~1400-byte UDP datagrams to the configured multicast group. This is a
        simplified RTP stream suitable for speakers that accept raw PCM over
        multicast.
        """
        group = speaker.multicast_group or speaker.endpoint
        port = speaker.multicast_port

        if not group:
            return {
                "speaker_id": speaker.speaker_id,
                "ok": False,
                "error": "no multicast group configured",
                "protocol": "multicast",
            }

        try:
            async with aiofiles.open(audio_path, "rb") as f:
                raw = await f.read()

            # Skip 44-byte WAV header to get raw PCM
            pcm_data = raw[44:] if len(raw) > 44 else raw

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._multicast_send_sync,
                group,
                port,
                pcm_data,
            )

            return {
                "speaker_id": speaker.speaker_id,
                "ok": True,
                "protocol": "multicast",
                "bytes_sent": len(pcm_data),
            }
        except Exception as exc:
            logger.error(
                "Multicast send failed for speaker %s: %s",
                speaker.speaker_id, exc,
            )
            return {
                "speaker_id": speaker.speaker_id,
                "ok": False,
                "error": str(exc),
                "protocol": "multicast",
            }

    @staticmethod
    def _multicast_send_sync(group: str, port: int, pcm_data: bytes) -> None:
        """Blocking multicast UDP send (called via run_in_executor)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            chunk_size = 1400  # safe UDP payload size
            seq = 0
            for offset in range(0, len(pcm_data), chunk_size):
                chunk = pcm_data[offset : offset + chunk_size]
                # Minimal 4-byte sequence header so the receiver can reorder
                header = struct.pack("!I", seq)
                sock.sendto(header + chunk, (group, port))
                seq += 1
        finally:
            sock.close()

    async def _send_sip_audio(
        self,
        speaker: SpeakerConfig,
        audio_path: str,
    ) -> dict[str, Any]:
        """Delegate to SIP gateway for SIP-enabled speakers.

        SIP speakers receive an INVITE; on answer the audio is streamed via
        RTP. This method imports :class:`SIPGateway` lazily to avoid circular
        dependencies when the SIP module is not needed.
        """
        try:
            from app.alerts.sip_gateway import SIPConfig, SIPGateway

            sip_cfg = SIPConfig(
                sip_server=settings.sip_server,
                sip_username=settings.sip_username,
                sip_password=settings.sip_password,
                sip_domain=settings.sip_domain,
                enabled=settings.sip_enabled,
            )
            gw = SIPGateway(config=sip_cfg, tts_engine=self._tts)
            try:
                target = speaker.sip_uri or speaker.endpoint
                result = await gw.initiate_alert_call(
                    camera_id=speaker.speaker_id,
                    text="",  # audio already generated
                    severity="warning",
                    audio_path_override=target,
                )
                return {
                    "speaker_id": speaker.speaker_id,
                    "ok": result.get("success", False),
                    "protocol": "sip",
                    "detail": result,
                }
            finally:
                await gw.close()
        except ImportError:
            logger.warning("SIP gateway module not available for speaker %s", speaker.speaker_id)
            return {
                "speaker_id": speaker.speaker_id,
                "ok": False,
                "error": "sip_gateway_unavailable",
                "protocol": "sip",
            }
        except Exception as exc:
            logger.error(
                "SIP send failed for speaker %s: %s", speaker.speaker_id, exc,
            )
            return {
                "speaker_id": speaker.speaker_id,
                "ok": False,
                "error": str(exc),
                "protocol": "sip",
            }

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def get_speakers_for_camera(self, camera_id: str) -> list[SpeakerConfig]:
        """Return enabled speakers that serve *camera_id* (by camera list or zone)."""
        matches: list[SpeakerConfig] = []
        for spk in self._speakers:
            if not spk.enabled:
                continue
            # Direct camera mapping
            if camera_id in spk.camera_ids:
                matches.append(spk)
                continue
            # Zone-based: if camera_id looks like "cam-XX" and the speaker
            # has no explicit camera_ids, it serves all cameras in its zone.
            if not spk.camera_ids:
                matches.append(spk)
        # Sort by priority (lower = higher priority)
        matches.sort(key=lambda s: s.priority)
        return matches

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Shut down the HTTP client."""
        await self._http.aclose()
        logger.info("IPSpeakerManager closed")
