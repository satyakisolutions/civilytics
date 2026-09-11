import logging
import time
import redis.asyncio as redis
from app.config.settings import settings

logger = logging.getLogger(__name__)


class AlertThrottle:
    def __init__(self, redis_url: str | None = None):
        self._redis = redis.from_url(redis_url or settings.redis_url)

    async def should_alert(
        self,
        camera_id: str,
        track_id: int | None = None,
        cooldown_s: int = 60,
        max_per_hour: int = 10,
    ) -> bool:
        now = int(time.time())
        # Per-camera cooldown
        camera_key = f"throttle:camera:{camera_id}"
        last_alert = await self._redis.get(camera_key)
        if last_alert and (now - int(last_alert)) < cooldown_s:
            logger.debug("Throttled (cooldown): camera=%s", camera_id)
            return False
        # Hourly limit
        hourly_key = f"throttle:hourly:{camera_id}"
        count = await self._redis.get(hourly_key)
        if count and int(count) >= max_per_hour:
            logger.debug("Throttled (hourly limit): camera=%s", camera_id)
            return False
        return True

    async def record_alert(self, camera_id: str, cooldown_s: int = 60):
        now = int(time.time())
        camera_key = f"throttle:camera:{camera_id}"
        await self._redis.set(camera_key, str(now), ex=cooldown_s)
        hourly_key = f"throttle:hourly:{camera_id}"
        pipe = await self._redis.pipeline()
        pipe.incr(hourly_key)
        pipe.expire(hourly_key, 3600)
        await pipe.execute()
        logger.debug("Recorded alert: camera=%s", camera_id)

    async def close(self):
        await self._redis.aclose()
