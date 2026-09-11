# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""MCP Server for WasteWatch Self-Healing Agent.

Exposes system monitoring, diagnosis, and healing as MCP tools
that any LLM (Claude, GPT, etc.) can call for interactive system management.

Server runs on port 9100 using a JSON-RPC 2.0 transport that follows the
MCP (Model Context Protocol) wire format.

Tools:
    get_system_status     — Full system health snapshot
    get_camera_status     — Per-camera health details
    get_healing_log       — Recent auto-heal actions with outcomes
    get_missed_events     — Detection misses, alert failures, dropped frames
    restart_service       — Restart a Docker service by name
    reconnect_camera      — Force camera reconnect in pipeline
    adjust_pipeline       — Change batch size, inference interval, thresholds
    clear_violation       — Manually clear a violation for a camera/track
    cleanup_storage       — Remove old evidence files
    diagnose_issue        — Get AI-ready diagnostic context for an issue

Resources:
    system://health       — Live system health (auto-updating)
    system://cameras      — Camera status list
    system://alerts       — Active alerts
    system://healing-log  — Healing action history
    system://misses       — Missed event summary
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.healer.main import SelfHealingAgent

logger = logging.getLogger(__name__)

# Services allowed to be restarted via MCP
ALLOWED_RESTART_SERVICES = frozenset({
    "vlm-worker", "tts-worker", "lpr-worker", "alert-svc",
    "sip-svc", "evidence-svc", "log-worker",
})

EVIDENCE_DIR = Path(os.getenv("WW_EVIDENCE_DIR", "/tmp/wastewatch/evidence"))


# ---------------------------------------------------------------------------
# Helper functions — Redis reads and Docker actions
# ---------------------------------------------------------------------------

def _get_redis(agent: SelfHealingAgent):
    """Get the agent's Redis client."""
    return agent._redis


def _read_all_health_sync(agent: SelfHealingAgent) -> dict:
    """Read full system health from Redis."""
    redis = _get_redis(agent)
    if not redis:
        return {"error": "Redis not available", "status": "unknown"}
    return agent._read_health_sync()


async def read_all_health(agent: SelfHealingAgent) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_all_health_sync, agent)


def _read_camera_health_sync(agent: SelfHealingAgent, camera_id: str) -> dict:
    redis = _get_redis(agent)
    if not redis:
        return {"error": "Redis not available"}
    raw = redis.get(f"ww:health:camera:{camera_id}")
    if raw:
        return json.loads(raw)
    return {"camera_id": camera_id, "status": "unknown", "error": "No health data"}


async def read_camera_health(agent: SelfHealingAgent, camera_id: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_camera_health_sync, agent, camera_id)


def _read_all_camera_health_sync(agent: SelfHealingAgent) -> list[dict]:
    redis = _get_redis(agent)
    if not redis:
        return []
    keys = redis.keys("ww:health:camera:*")
    cameras = []
    for key in keys:
        raw = redis.get(key)
        if raw:
            cameras.append(json.loads(raw))
    return cameras


async def read_all_camera_health(agent: SelfHealingAgent) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_all_camera_health_sync, agent)


def _read_active_alerts_sync(agent: SelfHealingAgent) -> list[dict]:
    redis = _get_redis(agent)
    if not redis:
        return []
    raw = redis.get("ww:alerts:active")
    if raw:
        return json.loads(raw)
    # Fall back to scanning alert keys
    keys = redis.keys("ww:alert:active:*")
    alerts = []
    for key in keys:
        raw = redis.get(key)
        if raw:
            alerts.append(json.loads(raw))
    return alerts


async def read_active_alerts(agent: SelfHealingAgent) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_active_alerts_sync, agent)


def generate_diagnosis_hint(
    health: dict,
    healing: list[dict],
    misses: list[dict],
) -> str:
    """Produce a diagnostic hint string for AI-assisted diagnosis."""
    hints = []

    status = health.get("status", health.get("state", ""))
    if status in ("down", "unhealthy"):
        hints.append("Component is completely down. Check logs with: docker logs <container>")

    if len(healing) > 3:
        hints.append(
            f"Component has been healed {len(healing)} times in 24h "
            f"— likely a recurring issue that needs manual investigation"
        )

    if len(healing) > 0:
        last = healing[-1]
        if not last.get("success", True):
            hints.append(f"Last healing attempt FAILED: {last.get('details', 'unknown')}")

    if misses:
        miss_types = sorted(set(m.get("event_type", "unknown") for m in misses))
        total = len(misses)
        hints.append(f"Related misses ({total}): {', '.join(miss_types)}")

    # GPU-specific hints
    if "gpu" in health:
        gpu = health["gpu"]
        if gpu.get("temperature_c", 0) > 80:
            hints.append(
                f"GPU temperature is {gpu['temperature_c']}C — check cooling and airflow"
            )
        if gpu.get("memory_used_pct", 0) > 90:
            hints.append(
                f"GPU memory at {gpu['memory_used_pct']}% — consider reducing model size or batch"
            )

    return " | ".join(hints) if hints else "No obvious issues detected"


# ---------------------------------------------------------------------------
# MCP Server — JSON-RPC 2.0 transport
# ---------------------------------------------------------------------------

# Tool definitions with MCP-compatible schemas
TOOL_DEFINITIONS = [
    {
        "name": "get_system_status",
        "description": "Get complete system health snapshot — all components, services, hardware.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_camera_status",
        "description": "Get health status for a specific camera or all cameras.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "camera_id": {
                    "type": "string",
                    "description": "Camera ID (e.g., 'cam-01'). Empty for all cameras.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_healing_log",
        "description": "Get recent self-healing actions with outcomes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Look back period in hours (default 24).",
                    "default": 24,
                },
                "component": {
                    "type": "string",
                    "description": "Filter by component (e.g., 'camera:cam-01', 'service:vlm').",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_missed_events",
        "description": (
            "Get missed detections, failed alerts, dropped frames, VLM timeouts. "
            "Types: missed_detection, failed_alert, dropped_frames, vlm_timeout, "
            "false_positive, recording_failure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Look back period (default 24).",
                    "default": 24,
                },
                "event_type": {
                    "type": "string",
                    "description": "Filter by event type.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "restart_service",
        "description": (
            "Restart a Docker service. Allowed: vlm-worker, tts-worker, "
            "lpr-worker, alert-svc, sip-svc, evidence-svc, log-worker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "description": "Docker service name (e.g., 'vlm-worker').",
                },
            },
            "required": ["service_name"],
        },
    },
    {
        "name": "reconnect_camera",
        "description": "Force reconnect a camera in the pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "camera_id": {
                    "type": "string",
                    "description": "Camera ID (e.g., 'cam-01').",
                },
            },
            "required": ["camera_id"],
        },
    },
    {
        "name": "adjust_pipeline",
        "description": "Adjust pipeline parameters. Only non-zero values are applied.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_size": {
                    "type": "integer",
                    "description": "nvinfer batch size (1-8). 0 = no change.",
                    "default": 0,
                },
                "inference_interval": {
                    "type": "integer",
                    "description": "nvinfer interval (1-10). 0 = no change.",
                    "default": 0,
                },
                "enter_threshold_s": {
                    "type": "number",
                    "description": "FSM enter threshold in seconds. 0 = no change.",
                    "default": 0,
                },
                "observe_threshold_s": {
                    "type": "number",
                    "description": "FSM observe threshold in seconds. 0 = no change.",
                    "default": 0,
                },
            },
            "required": [],
        },
    },
    {
        "name": "clear_violation",
        "description": "Manually clear a violation for a camera/track.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "camera_id": {
                    "type": "string",
                    "description": "Camera ID.",
                },
                "track_id": {
                    "type": "integer",
                    "description": "Track ID to clear.",
                },
            },
            "required": ["camera_id", "track_id"],
        },
    },
    {
        "name": "cleanup_storage",
        "description": "Remove evidence files older than N days.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_to_keep": {
                    "type": "integer",
                    "description": "Keep files newer than this (default 30).",
                    "default": 30,
                },
            },
            "required": [],
        },
    },
    {
        "name": "diagnose_issue",
        "description": (
            "Get diagnostic context for a component — recent health, healing history, "
            "related misses. Returns AI-ready context for diagnosis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "description": "Component to diagnose (e.g., 'camera:cam-01', 'service:vlm', 'system:gpu').",
                },
            },
            "required": ["component"],
        },
    },
]

# Resource definitions
RESOURCE_DEFINITIONS = [
    {
        "uri": "system://health",
        "name": "System Health",
        "description": "Live system health status — all components, services, hardware.",
        "mimeType": "application/json",
    },
    {
        "uri": "system://cameras",
        "name": "Camera Status",
        "description": "Camera status list — per-camera health data.",
        "mimeType": "application/json",
    },
    {
        "uri": "system://alerts",
        "name": "Active Alerts",
        "description": "Currently active alerts across all cameras.",
        "mimeType": "application/json",
    },
    {
        "uri": "system://healing-log",
        "name": "Healing Log",
        "description": "Self-healing action history (last 24 hours).",
        "mimeType": "application/json",
    },
    {
        "uri": "system://misses",
        "name": "Missed Events",
        "description": "Missed event summary — detections, alerts, frames (last 24 hours).",
        "mimeType": "application/json",
    },
]


class MCPServer:
    """JSON-RPC 2.0 server implementing the MCP protocol.

    Serves tool definitions, handles tool calls, and exposes resources
    over HTTP/SSE on port 9100 for LLM-driven system management.

    If the ``mcp`` Python SDK is available, uses it as the transport.
    Otherwise, runs a lightweight asyncio HTTP server that speaks the
    MCP JSON-RPC wire format directly.
    """

    def __init__(self, agent: SelfHealingAgent, host: str = "0.0.0.0", port: int = 9100) -> None:
        self._agent = agent
        self._host = host
        self._port = port
        self._tool_handlers: dict[str, Any] = {
            "get_system_status": self._handle_get_system_status,
            "get_camera_status": self._handle_get_camera_status,
            "get_healing_log": self._handle_get_healing_log,
            "get_missed_events": self._handle_get_missed_events,
            "restart_service": self._handle_restart_service,
            "reconnect_camera": self._handle_reconnect_camera,
            "adjust_pipeline": self._handle_adjust_pipeline,
            "clear_violation": self._handle_clear_violation,
            "cleanup_storage": self._handle_cleanup_storage,
            "diagnose_issue": self._handle_diagnose_issue,
        }
        self._resource_handlers: dict[str, Any] = {
            "system://health": self._resource_health,
            "system://cameras": self._resource_cameras,
            "system://alerts": self._resource_alerts,
            "system://healing-log": self._resource_healing_log,
            "system://misses": self._resource_misses,
        }

    async def serve(self) -> None:
        """Start the MCP server. Tries the ``mcp`` SDK first, falls back to raw HTTP."""
        try:
            await self._serve_native_mcp()
        except ImportError:
            logger.info("mcp SDK not installed — using built-in JSON-RPC server")
            await self._serve_jsonrpc()

    # ------------------------------------------------------------------
    # Native MCP SDK transport
    # ------------------------------------------------------------------

    async def _serve_native_mcp(self) -> None:
        """Serve using the ``mcp`` Python SDK with SSE transport."""
        from mcp.server import Server
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route
        import uvicorn

        server = Server("wastewatch-healer")

        # Register tools
        @server.list_tools()
        async def list_tools():
            from mcp.types import Tool
            return [Tool(**t) for t in TOOL_DEFINITIONS]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict):
            from mcp.types import TextContent
            handler = self._tool_handlers.get(name)
            if not handler:
                return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
            result = await handler(arguments)
            return [TextContent(type="text", text=json.dumps(result, default=str))]

        # Register resources
        @server.list_resources()
        async def list_resources():
            from mcp.types import Resource
            return [Resource(**r) for r in RESOURCE_DEFINITIONS]

        @server.read_resource()
        async def read_resource(uri: str):
            handler = self._resource_handlers.get(str(uri))
            if not handler:
                return json.dumps({"error": f"Unknown resource: {uri}"})
            result = await handler()
            return json.dumps(result, default=str)

        # SSE transport
        sse = SseServerTransport("/messages")

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())

        async def handle_messages(request):
            await sse.handle_post_message(request.scope, request.receive, request._send)

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Route("/messages", endpoint=handle_messages, methods=["POST"]),
            ],
        )

        config = uvicorn.Config(app, host=self._host, port=self._port, log_level="info")
        server_instance = uvicorn.Server(config)
        await server_instance.serve()

    # ------------------------------------------------------------------
    # Built-in JSON-RPC 2.0 server (MCP wire format fallback)
    # ------------------------------------------------------------------

    async def _serve_jsonrpc(self) -> None:
        """Serve a lightweight JSON-RPC 2.0 HTTP server following MCP wire format."""
        from asyncio import start_server

        server = await asyncio.start_server(
            self._handle_http_connection, self._host, self._port,
        )
        logger.info("MCP JSON-RPC server listening on %s:%d", self._host, self._port)
        async with server:
            await server.serve_forever()

    async def _handle_http_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one HTTP connection with JSON-RPC request."""
        try:
            # Read HTTP request
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return

            # Read headers
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if ":" in decoded:
                    key, val = decoded.split(":", 1)
                    headers[key.strip().lower()] = val.strip()

            # Parse request line
            parts = request_line.decode("utf-8", errors="replace").strip().split()
            method = parts[0] if parts else "GET"
            path = parts[1] if len(parts) > 1 else "/"

            # CORS headers for browser-based MCP clients
            cors_headers = (
                "Access-Control-Allow-Origin: *\r\n"
                "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                "Access-Control-Allow-Headers: Content-Type\r\n"
            )

            if method == "OPTIONS":
                response = f"HTTP/1.1 204 No Content\r\n{cors_headers}\r\n"
                writer.write(response.encode())
                await writer.drain()
                writer.close()
                return

            # Read body
            content_length = int(headers.get("content-length", "0"))
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            # Route
            if method == "GET" and path == "/health":
                result = await self._handle_get_system_status({})
                response_body = json.dumps(result, default=str).encode()
                response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(response_body)}\r\n"
                    f"{cors_headers}\r\n"
                ).encode() + response_body

            elif method == "POST" and path in ("/jsonrpc", "/mcp", "/"):
                response_body = await self._handle_jsonrpc(body)
                response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(response_body)}\r\n"
                    f"{cors_headers}\r\n"
                ).encode() + response_body

            elif method == "GET" and path == "/tools":
                result = {"tools": TOOL_DEFINITIONS}
                response_body = json.dumps(result).encode()
                response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(response_body)}\r\n"
                    f"{cors_headers}\r\n"
                ).encode() + response_body

            elif method == "GET" and path == "/resources":
                result = {"resources": RESOURCE_DEFINITIONS}
                response_body = json.dumps(result).encode()
                response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(response_body)}\r\n"
                    f"{cors_headers}\r\n"
                ).encode() + response_body

            else:
                body_404 = b'{"error":"Not found"}'
                response = (
                    f"HTTP/1.1 404 Not Found\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body_404)}\r\n"
                    f"{cors_headers}\r\n"
                ).encode() + body_404

            writer.write(response)
            await writer.drain()

        except Exception:
            logger.exception("Error handling MCP HTTP connection")
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_jsonrpc(self, body: bytes) -> bytes:
        """Handle a JSON-RPC 2.0 request following MCP protocol."""
        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            return json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            }).encode()

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {
                        "name": "wastewatch-healer",
                        "version": "1.0.0",
                    },
                }
            elif method == "tools/list":
                result = {"tools": TOOL_DEFINITIONS}
            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                handler = self._tool_handlers.get(tool_name)
                if not handler:
                    result = {
                        "content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"})}],
                        "isError": True,
                    }
                else:
                    tool_result = await handler(arguments)
                    result = {
                        "content": [{"type": "text", "text": json.dumps(tool_result, default=str)}],
                        "isError": False,
                    }
            elif method == "resources/list":
                result = {"resources": RESOURCE_DEFINITIONS}
            elif method == "resources/read":
                uri = params.get("uri", "")
                handler = self._resource_handlers.get(uri)
                if not handler:
                    result = {
                        "contents": [{"uri": uri, "text": json.dumps({"error": f"Unknown resource: {uri}"})}],
                    }
                else:
                    data = await handler()
                    result = {
                        "contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, default=str)}],
                    }
            elif method == "notifications/initialized":
                # Client acknowledgement — no response needed for notifications
                return json.dumps({
                    "jsonrpc": "2.0",
                    "result": {},
                    "id": req_id,
                }).encode()
            else:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": req_id,
                }).encode()

        except Exception as exc:
            logger.exception("Error handling JSON-RPC method %s", method)
            return json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
                "id": req_id,
            }).encode()

        return json.dumps({
            "jsonrpc": "2.0",
            "result": result,
            "id": req_id,
        }).encode()

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _handle_get_system_status(self, arguments: dict) -> dict:
        """Full system health snapshot."""
        return await read_all_health(self._agent)

    async def _handle_get_camera_status(self, arguments: dict) -> dict | list:
        """Per-camera health details."""
        camera_id = arguments.get("camera_id", "")
        if camera_id:
            return await read_camera_health(self._agent, camera_id)
        return await read_all_camera_health(self._agent)

    async def _handle_get_healing_log(self, arguments: dict) -> list[dict]:
        """Recent self-healing actions."""
        hours = arguments.get("hours", 24)
        component = arguments.get("component", "")
        return self._agent.get_healing_history(hours=hours, component=component)

    async def _handle_get_missed_events(self, arguments: dict) -> dict:
        """Missed events summary."""
        hours = arguments.get("hours", 24)
        event_type = arguments.get("event_type", "")
        events = self._agent.get_miss_events(hours=hours, event_type=event_type)
        by_type: dict[str, int] = {}
        for e in events:
            et = e.get("event_type", "unknown")
            by_type[et] = by_type.get(et, 0) + 1
        return {
            "hours": hours,
            "total": len(events),
            "by_type": by_type,
            "events": events,
        }

    async def _handle_restart_service(self, arguments: dict) -> dict:
        """Restart a Docker service."""
        service_name = arguments.get("service_name", "")
        if service_name not in ALLOWED_RESTART_SERVICES:
            return {
                "error": f"Cannot restart '{service_name}'. Allowed: {sorted(ALLOWED_RESTART_SERVICES)}",
                "success": False,
            }

        from services.healer.main import HealingAction
        action = await self._agent._heal_restart_service({
            "component": f"service:{service_name}",
            "issue": "mcp_manual_restart",
        })
        # Mark as manual (not automatic)
        action.auto = False
        await self._agent._log_action(action)
        return {
            "success": action.success,
            "service": service_name,
            "details": action.details,
        }

    async def _handle_reconnect_camera(self, arguments: dict) -> dict:
        """Force reconnect a camera."""
        camera_id = arguments.get("camera_id", "")
        if not camera_id:
            return {"error": "camera_id is required", "success": False}

        from services.healer.main import HealingAction
        action = await self._agent._heal_camera_reconnect({
            "component": f"camera:{camera_id}",
            "issue": "mcp_manual_reconnect",
        })
        action.auto = False
        await self._agent._log_action(action)
        return {
            "success": action.success,
            "camera_id": camera_id,
            "details": action.details,
        }

    async def _handle_adjust_pipeline(self, arguments: dict) -> dict:
        """Adjust pipeline parameters."""
        batch_size = arguments.get("batch_size", 0)
        inference_interval = arguments.get("inference_interval", 0)
        enter_threshold_s = arguments.get("enter_threshold_s", 0)
        observe_threshold_s = arguments.get("observe_threshold_s", 0)

        if not any([batch_size, inference_interval, enter_threshold_s, observe_threshold_s]):
            return {"error": "At least one non-zero parameter is required", "success": False}

        # Validate ranges
        if batch_size and not (1 <= batch_size <= 8):
            return {"error": "batch_size must be 1-8", "success": False}
        if inference_interval and not (1 <= inference_interval <= 10):
            return {"error": "inference_interval must be 1-10", "success": False}

        config_update: dict[str, Any] = {"reason": "mcp_manual_adjust"}
        if batch_size:
            config_update["batch_size"] = batch_size
        if inference_interval:
            config_update["inference_interval"] = inference_interval
        if enter_threshold_s:
            config_update["enter_threshold_s"] = enter_threshold_s
        if observe_threshold_s:
            config_update["observe_threshold_s"] = observe_threshold_s

        success = self._agent._pipeline_notifier._publish({
            "action": "config_update",
            "data": config_update,
        })

        # Also persist overrides in Redis with 30-minute TTL
        redis = _get_redis(self._agent)
        if redis:
            try:
                for key, val in config_update.items():
                    if key != "reason":
                        redis.set(f"ww:config:{key}_override", str(val), ex=1800)
            except Exception:
                pass

        return {
            "success": success,
            "applied": {k: v for k, v in config_update.items() if k != "reason"},
            "details": "Config update sent to pipeline. Override expires in 30 minutes.",
        }

    async def _handle_clear_violation(self, arguments: dict) -> dict:
        """Manually clear a violation."""
        camera_id = arguments.get("camera_id", "")
        track_id = arguments.get("track_id")
        if not camera_id or track_id is None:
            return {"error": "camera_id and track_id are required", "success": False}

        success = self._agent._pipeline_notifier.clear_violation(
            camera_id=camera_id,
            track_id=int(track_id),
            reason="mcp_manual_clear",
        )
        return {
            "success": success,
            "camera_id": camera_id,
            "track_id": track_id,
            "details": "Violation clear command sent to pipeline.",
        }

    async def _handle_cleanup_storage(self, arguments: dict) -> dict:
        """Remove old evidence files."""
        days_to_keep = arguments.get("days_to_keep", 30)
        if days_to_keep < 1:
            return {"error": "days_to_keep must be >= 1", "success": False}

        loop = asyncio.get_event_loop()
        try:
            deleted, freed_mb = await loop.run_in_executor(
                None, self._agent._cleanup_evidence_sync, days_to_keep,
            )
            from services.healer.main import HealingAction
            action = HealingAction(
                timestamp=time.time(),
                component="system:storage",
                issue="mcp_manual_cleanup",
                action_taken=f"cleanup_storage:days={days_to_keep}",
                success=True,
                details=f"Deleted {deleted} files, freed {freed_mb:.1f} MB",
                auto=False,
            )
            await self._agent._log_action(action)
            return {
                "success": True,
                "deleted_files": deleted,
                "freed_mb": round(freed_mb, 1),
                "days_kept": days_to_keep,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _handle_diagnose_issue(self, arguments: dict) -> dict:
        """Get diagnostic context for a component."""
        component = arguments.get("component", "")
        if not component:
            return {"error": "component is required"}

        # Gather health data for the component
        health: dict = {}
        if component.startswith("camera:"):
            camera_id = component.split(":", 1)[1]
            health = await read_camera_health(self._agent, camera_id)
        elif component.startswith("system:"):
            all_health = await read_all_health(self._agent)
            subsystem = component.split(":", 1)[1]
            health = all_health.get("system", {}).get(subsystem, {})
        elif component.startswith("service:"):
            all_health = await read_all_health(self._agent)
            svc = component.split(":", 1)[1]
            health = all_health.get("services", {}).get(svc, {})
            # Also check models
            if not health:
                health = all_health.get("models", {}).get(svc, {})
        else:
            health = {"status": "unknown", "note": f"Unrecognized component format: {component}"}

        healing = self._agent.get_healing_history(hours=24, component=component)
        misses = self._agent.get_miss_events(hours=24)
        # Filter misses relevant to this component
        relevant_misses = [m for m in misses if component in m.get("component", "")]

        hint = generate_diagnosis_hint(health, healing, relevant_misses)

        return {
            "component": component,
            "current_health": health,
            "recent_healing_actions": healing[-20:],  # Last 20
            "healing_count_24h": len(healing),
            "recent_misses": relevant_misses[-50:],  # Last 50
            "miss_count_24h": len(relevant_misses),
            "diagnosis_hint": hint,
        }

    # ------------------------------------------------------------------
    # Resource handlers
    # ------------------------------------------------------------------

    async def _resource_health(self) -> dict:
        return await read_all_health(self._agent)

    async def _resource_cameras(self) -> list[dict]:
        return await read_all_camera_health(self._agent)

    async def _resource_alerts(self) -> list[dict]:
        return await read_active_alerts(self._agent)

    async def _resource_healing_log(self) -> list[dict]:
        return self._agent.get_healing_history(hours=24)

    async def _resource_misses(self) -> dict:
        return await self._agent.get_miss_summary(hours=24)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_mcp_server(agent: SelfHealingAgent) -> MCPServer:
    """Create and return an MCPServer instance bound to the given agent."""
    host = os.getenv("WW_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("WW_MCP_PORT", "9100"))
    return MCPServer(agent, host=host, port=port)
