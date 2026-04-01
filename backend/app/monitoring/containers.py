"""Docker container health monitoring."""
import logging

logger = logging.getLogger(__name__)


async def get_container_stats() -> list[dict]:
    """Get Docker container stats via docker CLI (simpler than Docker API)."""
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}","net":"{{.NetIO}}","status":"running"}'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return [{"error": "docker stats failed"}]

        import json
        containers = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return containers
    except FileNotFoundError:
        return [{"error": "docker not found"}]
    except Exception as e:
        return [{"error": str(e)}]
