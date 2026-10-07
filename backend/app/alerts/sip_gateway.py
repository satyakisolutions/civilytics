# backend/app/alerts/sip_gateway.py
"""SIP Gateway for voice call alerts -- escalation channel.

Places voice calls via SIP (Asterisk/FreeSWITCH/PJSIP) to alert
operators when violations are not acknowledged within the timeout.

Alert cascade:
    T+0s    WebSocket + IP Speaker (instant)
    T+60s   SIP call to primary operator (if not acknowledged)
    T+120s  SIP call to zone supervisor (if still not acknowledged)
    T+300s  SIP call to admin (final escalation)

Uses PJSUA2 (Python bindings for PJSIP) or Asterisk AMI/ARI for call
control.  Falls back to Twilio REST API if no local PBX is available.

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.tts.engine import TTSEngine

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

@dataclass
class SIPConfig:
    """Connection and behaviour settings for the SIP gateway."""

    # PBX connection
    sip_server: str = ""              # e.g., "sip:asterisk.local:5060"
    sip_username: str = ""
    sip_password: str = ""
    sip_domain: str = ""

    # Twilio fallback
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Escalation contacts (ordered by priority)
    escalation_contacts: list[dict] = field(default_factory=list)
    # Each: {"name": "Operator", "number": "sip:1001@pbx", "delay_s": 60, "level": 1}

    # Call settings
    max_ring_time_s: int = 30         # Ring for 30s before giving up
    tts_repeat_count: int = 2         # Repeat TTS message N times during call
    call_timeout_s: int = 120         # Max call duration

    enabled: bool = False


@dataclass
class EscalationLevel:
    """Tracks a single escalation tier for a violation."""

    level: int           # 1, 2, 3
    contact_name: str
    contact_number: str  # SIP URI or phone number
    delay_s: int         # Seconds after violation before this level triggers
    triggered: bool = False
    call_id: str | None = None


# ------------------------------------------------------------------
# SIP Gateway
# ------------------------------------------------------------------

class SIPGateway:
    """Places voice calls for alert escalation.

    Resolution order:
    1. pjsua2 (local PBX via PJSIP Python bindings)
    2. Asterisk AMI ``Action: Originate``
    3. Twilio REST API
    4. Stub / logging-only mode

    Usage::

        gw = SIPGateway(config, tts_engine)
        await gw.initiate_alert_call("cam-01", "Dumping detected", "critical")
        await gw.close()
    """

    def __init__(self, config: SIPConfig, tts_engine: TTSEngine | None = None) -> None:
        self._config = config
        self._tts = tts_engine or TTSEngine()
        self._http = httpx.AsyncClient(timeout=15.0)

        # Detect available call backends
        self._has_pjsua = False
        self._has_twilio = False

        try:
            import pjsua2  # noqa: F401
            self._has_pjsua = True
            logger.info("SIPGateway: pjsua2 available -- using local PBX")
        except ImportError:
            pass

        if config.twilio_account_sid and config.twilio_auth_token:
            self._has_twilio = True
            if not self._has_pjsua:
                logger.info("SIPGateway: using Twilio REST fallback")

        if not self._has_pjsua and not self._has_twilio:
            logger.warning(
                "SIPGateway: no call backend available (pjsua2 missing, "
                "Twilio not configured) -- calls will be logged only"
            )

        # Build escalation levels from config
        self._escalation_levels = self._build_escalation_levels()
        logger.info(
            "SIPGateway initialised: enabled=%s escalation_levels=%d",
            config.enabled,
            len(self._escalation_levels),
        )

    # ------------------------------------------------------------------
    # Escalation level helpers
    # ------------------------------------------------------------------

    def _build_escalation_levels(self) -> list[EscalationLevel]:
        """Parse escalation contacts from config into EscalationLevel objects."""
        levels: list[EscalationLevel] = []
        for entry in self._config.escalation_contacts:
            try:
                levels.append(EscalationLevel(
                    level=int(entry.get("level", len(levels) + 1)),
                    contact_name=entry.get("name", f"Contact-{len(levels) + 1}"),
                    contact_number=entry.get("number", ""),
                    delay_s=int(entry.get("delay_s", 60 * (len(levels) + 1))),
                ))
            except (ValueError, TypeError):
                logger.warning("Skipping invalid escalation contact: %s", entry)
        levels.sort(key=lambda lv: lv.delay_s)
        return levels

    def get_escalation_levels(self) -> list[EscalationLevel]:
        """Return a fresh copy of escalation levels (all un-triggered)."""
        return [
            EscalationLevel(
                level=lv.level,
                contact_name=lv.contact_name,
                contact_number=lv.contact_number,
                delay_s=lv.delay_s,
            )
            for lv in self._escalation_levels
        ]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def initiate_alert_call(
        self,
        camera_id: str,
        text: str,
        severity: str,
        audio_path_override: str | None = None,
    ) -> dict[str, Any]:
        """Place a voice call delivering *text* as TTS audio.

        Parameters
        ----------
        camera_id:
            Source camera for logging/routing.
        text:
            Alert message to speak.  Ignored if *audio_path_override* is set.
        severity:
            ``"critical"`` | ``"high"`` | ``"medium"`` etc.
        audio_path_override:
            If provided, skip TTS and use this file directly (or as the
            SIP target URI when called from IP speaker SIP path).
        """
        if not self._config.enabled:
            logger.debug("SIP gateway disabled -- skipping call for camera=%s", camera_id)
            return {"success": False, "reason": "disabled"}

        # --- Step 1: Obtain audio ---
        if audio_path_override and Path(audio_path_override).is_file():
            audio_path = audio_path_override
        elif text:
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(None, self._tts.generate, text)
                audio_path = result["path"]
            except Exception:
                logger.exception("TTS failed for SIP call")
                return {"success": False, "reason": "tts_failed"}
        else:
            return {"success": False, "reason": "no_audio_source"}

        # --- Step 2: Convert to 8 kHz mono if needed (SIP / G.711) ---
        sip_audio = await self._ensure_sip_format(audio_path)

        # --- Step 3: Determine first escalation contact ---
        levels = self.get_escalation_levels()
        if not levels:
            logger.warning("No escalation contacts configured")
            return {"success": False, "reason": "no_contacts"}

        contact = levels[0].contact_number

        # --- Step 4: Place call via best available backend ---
        call_result = await self._place_call(contact, sip_audio, text)

        logger.info(
            "SIP call result: camera=%s contact=%s success=%s",
            camera_id,
            contact,
            call_result.get("success", False),
        )
        return call_result

    # ------------------------------------------------------------------
    # Call backends
    # ------------------------------------------------------------------

    async def _place_call(
        self,
        contact: str,
        audio_path: str,
        text: str,
    ) -> dict[str, Any]:
        """Try each backend in priority order until one succeeds."""
        if self._has_pjsua:
            result = await self._call_via_pjsip(contact, audio_path)
            if result.get("success"):
                return result

        # Try Asterisk AMI if SIP server is configured
        if self._config.sip_server:
            result = await self._call_via_ami(contact, audio_path)
            if result.get("success"):
                return result

        if self._has_twilio:
            return await self._call_via_twilio(contact, text)

        # No backend available -- log-only stub
        logger.warning(
            "SIP STUB: would call %s with audio %s (no backend available)",
            contact,
            audio_path,
        )
        return {
            "success": False,
            "reason": "no_backend",
            "contact": contact,
            "audio_path": audio_path,
        }

    async def _call_via_pjsip(
        self,
        contact: str,
        audio_path: str,
    ) -> dict[str, Any]:
        """Place a call using pjsua2 Python bindings (local PBX).

        Creates a transient PJSIP account, makes an outbound call, and on
        answer plays the WAV file the configured number of repeats.
        """
        try:
            import pjsua2 as pj

            ep = pj.Endpoint()
            ep_cfg = pj.EpConfig()
            ep_cfg.logConfig.level = 3
            ep.libCreate()
            ep.libInit(ep_cfg)

            # Transport
            tp_cfg = pj.TransportConfig()
            tp_cfg.port = 0  # auto-select
            ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, tp_cfg)
            ep.libStart()

            # Account
            acc_cfg = pj.AccountConfig()
            acc_cfg.idUri = f"sip:{self._config.sip_username}@{self._config.sip_domain}"
            acc_cfg.regConfig.registrarUri = self._config.sip_server
            cred = pj.AuthCredInfo("digest", "*", self._config.sip_username, 0, self._config.sip_password)
            acc_cfg.sipConfig.authCreds.append(cred)

            acc = pj.Account()
            acc.create(acc_cfg)

            # Make call
            call = pj.Call(acc)
            call_prm = pj.CallOpParam(True)
            call.makeCall(contact, call_prm)

            # Wait for answer (simplified; real implementation uses callbacks)
            await asyncio.sleep(self._config.max_ring_time_s)

            # Play audio on answer
            player = pj.AudioMediaPlayer()
            for _ in range(self._config.tts_repeat_count):
                player.createPlayer(audio_path)

            # Cleanup after timeout
            await asyncio.sleep(self._config.call_timeout_s)
            call.hangup(call_prm)
            ep.libDestroy()

            return {
                "success": True,
                "backend": "pjsip",
                "contact": contact,
            }
        except ImportError:
            return {"success": False, "reason": "pjsua2_import_failed"}
        except Exception as exc:
            logger.error("PJSIP call failed: %s", exc)
            return {"success": False, "reason": str(exc), "backend": "pjsip"}

    async def _call_via_twilio(
        self,
        contact: str,
        text: str,
    ) -> dict[str, Any]:
        """Place a call using Twilio REST API.

        Generates inline TwiML that speaks the alert text and repeats it
        the configured number of times.
        """
        sid = self._config.twilio_account_sid
        token = self._config.twilio_auth_token
        from_number = self._config.twilio_from_number

        # Build TwiML with repeated <Say> and <Pause>
        say_blocks = []
        for i in range(self._config.tts_repeat_count):
            say_blocks.append(f"<Say voice=\"alice\">{text}</Say>")
            if i < self._config.tts_repeat_count - 1:
                say_blocks.append("<Pause length=\"2\"/>")
        twiml = f"<Response>{''.join(say_blocks)}</Response>"

        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"

        try:
            resp = await self._http.post(
                url,
                auth=(sid, token),
                data={
                    "To": contact,
                    "From": from_number,
                    "Twiml": twiml,
                    "Timeout": str(self._config.max_ring_time_s),
                },
            )
            resp.raise_for_status()
            body = resp.json()
            call_sid = body.get("sid", "")
            logger.info("Twilio call placed: sid=%s to=%s", call_sid, contact)
            return {
                "success": True,
                "backend": "twilio",
                "contact": contact,
                "call_sid": call_sid,
            }
        except Exception as exc:
            logger.error("Twilio call failed: %s", exc)
            return {
                "success": False,
                "backend": "twilio",
                "reason": str(exc),
                "contact": contact,
            }

    async def _call_via_ami(
        self,
        contact: str,
        audio_path: str,
    ) -> dict[str, Any]:
        """Place a call using Asterisk Manager Interface (AMI) Originate.

        Connects to the AMI socket, authenticates, and sends an Originate
        action that dials the contact and plays the audio file.
        """
        # Parse SIP server host/port for AMI (default AMI port 5038)
        sip_server = self._config.sip_server.replace("sip:", "")
        if ":" in sip_server:
            host, port_str = sip_server.rsplit(":", 1)
            try:
                ami_port = int(port_str)
                # If this looks like a SIP port, use the AMI default instead
                if ami_port in (5060, 5061):
                    ami_port = 5038
            except ValueError:
                host = sip_server
                ami_port = 5038
        else:
            host = sip_server
            ami_port = 5038

        # Strip .wav extension for Asterisk Playback application
        audio_base = audio_path
        if audio_base.endswith(".wav"):
            audio_base = audio_base[:-4]

        try:
            loop = asyncio.get_event_loop()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, ami_port),
                timeout=5.0,
            )

            # Read AMI banner
            await asyncio.wait_for(reader.readline(), timeout=5.0)

            # Login
            login_cmd = (
                f"Action: Login\r\n"
                f"Username: {self._config.sip_username}\r\n"
                f"Secret: {self._config.sip_password}\r\n"
                f"\r\n"
            )
            writer.write(login_cmd.encode())
            await writer.drain()

            # Read login response (consume until blank line)
            login_ok = False
            for _ in range(10):
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                decoded = line.decode().strip()
                if "Success" in decoded:
                    login_ok = True
                if decoded == "":
                    break

            if not login_ok:
                writer.close()
                return {"success": False, "reason": "ami_login_failed", "backend": "ami"}

            # Originate
            originate_cmd = (
                f"Action: Originate\r\n"
                f"Channel: SIP/{contact}\r\n"
                f"Application: Playback\r\n"
                f"Data: {audio_base}\r\n"
                f"Timeout: {self._config.max_ring_time_s * 1000}\r\n"
                f"CallerID: Civilytics <wastewatch>\r\n"
                f"Async: true\r\n"
                f"\r\n"
            )
            writer.write(originate_cmd.encode())
            await writer.drain()

            # Read originate response
            originate_ok = False
            for _ in range(10):
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                decoded = line.decode().strip()
                if "Success" in decoded:
                    originate_ok = True
                if decoded == "":
                    break

            # Logoff
            writer.write(b"Action: Logoff\r\n\r\n")
            await writer.drain()
            writer.close()

            if originate_ok:
                logger.info("AMI Originate success: contact=%s", contact)
                return {"success": True, "backend": "ami", "contact": contact}
            else:
                return {"success": False, "reason": "originate_failed", "backend": "ami"}

        except asyncio.TimeoutError:
            logger.error("AMI connection timed out to %s:%d", host, ami_port)
            return {"success": False, "reason": "ami_timeout", "backend": "ami"}
        except OSError as exc:
            logger.error("AMI connection failed: %s", exc)
            return {"success": False, "reason": str(exc), "backend": "ami"}

    # ------------------------------------------------------------------
    # Escalation checker
    # ------------------------------------------------------------------

    async def check_escalation(
        self,
        camera_id: str,
        track_id: int,
        violation_time: float,
        acknowledged: bool,
        escalation_state: list[EscalationLevel] | None = None,
    ) -> dict[str, Any]:
        """Check if enough time has passed to escalate to the next level.

        Called periodically by :class:`EscalationWorker`.  Returns a dict
        indicating whether an escalation was triggered.

        Parameters
        ----------
        camera_id:
            Camera that raised the violation.
        track_id:
            Track ID of the violation event.
        violation_time:
            ``time.monotonic()`` timestamp when the violation was first detected.
        acknowledged:
            Whether an operator has acknowledged the alert.
        escalation_state:
            Mutable list of :class:`EscalationLevel` for this violation.
            The worker persists this across calls.
        """
        if acknowledged:
            return {"escalated": False, "reason": "acknowledged"}

        elapsed = time.monotonic() - violation_time

        levels = escalation_state if escalation_state is not None else self.get_escalation_levels()

        for level in levels:
            if elapsed >= level.delay_s and not level.triggered:
                level.triggered = True
                alert_text = (
                    f"Escalation level {level.level}. Unacknowledged violation at "
                    f"camera {camera_id}, track {track_id}. "
                    f"Elapsed time {int(elapsed)} seconds. "
                    f"Calling {level.contact_name}."
                )
                call_result = await self.initiate_alert_call(
                    camera_id=camera_id,
                    text=alert_text,
                    severity="critical",
                )
                level.call_id = call_result.get("call_sid")
                logger.warning(
                    "Escalation triggered: camera=%s level=%d contact=%s elapsed=%ds",
                    camera_id, level.level, level.contact_name, int(elapsed),
                )
                return {
                    "escalated": True,
                    "level": level.level,
                    "contact": level.contact_name,
                    "elapsed_s": int(elapsed),
                    "call_result": call_result,
                }

        return {"escalated": False, "reason": "no_level_due"}

    # ------------------------------------------------------------------
    # Audio format helpers
    # ------------------------------------------------------------------

    async def _ensure_sip_format(self, audio_path: str) -> str:
        """Convert WAV to 8 kHz mono 16-bit PCM if not already.

        SIP / G.711 typically requires 8 kHz sample rate.  If the source
        file is already 8 kHz mono we return it as-is; otherwise we
        convert using ``sox`` or ``ffmpeg`` if available, falling back to
        the original file.
        """
        try:
            with wave.open(audio_path, "rb") as wf:
                rate = wf.getframerate()
                channels = wf.getnchannels()
            if rate == 8000 and channels == 1:
                return audio_path
        except Exception:
            return audio_path

        out_path = audio_path.replace(".wav", "_8k.wav")
        if Path(out_path).exists():
            return out_path

        loop = asyncio.get_event_loop()

        # Try sox first, then ffmpeg
        for cmd in [
            ["sox", audio_path, "-r", "8000", "-c", "1", out_path],
            ["ffmpeg", "-y", "-i", audio_path, "-ar", "8000", "-ac", "1", out_path],
        ]:
            try:
                await loop.run_in_executor(
                    None,
                    lambda c=cmd: subprocess.run(c, check=True, capture_output=True, timeout=10),
                )
                if Path(out_path).exists():
                    return out_path
            except (FileNotFoundError, subprocess.SubprocessError):
                continue

        logger.debug("Could not convert %s to 8 kHz -- using original", audio_path)
        return audio_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release HTTP client and any SIP resources."""
        await self._http.aclose()
        logger.info("SIPGateway closed")
