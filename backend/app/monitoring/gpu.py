"""GPU health monitoring via nvidia-smi."""
import logging
import subprocess
import json

logger = logging.getLogger(__name__)


async def get_gpu_stats() -> list[dict]:
    """Parse nvidia-smi for GPU stats. Returns list of GPU info dicts."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,utilization.memory,memory.total,memory.used,memory.free,temperature.gpu,power.draw",
             "--format=csv,nounits,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            logger.error("nvidia-smi failed: %s", result.stderr)
            return [{"error": "nvidia-smi failed"}]

        gpus = []
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 9:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "gpu_utilization_pct": float(parts[2]),
                    "memory_utilization_pct": float(parts[3]),
                    "memory_total_mb": float(parts[4]),
                    "memory_used_mb": float(parts[5]),
                    "memory_free_mb": float(parts[6]),
                    "temperature_c": float(parts[7]),
                    "power_draw_w": float(parts[8]) if parts[8] != "[N/A]" else None,
                })
        return gpus
    except FileNotFoundError:
        return [{"error": "nvidia-smi not found"}]
    except Exception as e:
        return [{"error": str(e)}]
