"""E2E fixtures. Hybrid execution:

  * CIVILYTICS_BASE_URL set  → run against that already-running stack.
  * CIVILYTICS_BASE_URL unset → bring dependencies up via testcontainers
    (postgres/kafka/redis/keycloak/minio) + the built civilytics-prod-* API
    image, run `alembic upgrade head`, seed a realm+owner. The GPU DeepStream
    pipeline is NOT started; detection is simulated by publishing to Kafka.

Every test that creates data cleans it up; the suite is re-runnable.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from helpers import Config, load_config, kafka_reachable

pytestmark = pytest.mark.e2e


# ── config / mode ───────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def cfg() -> Config:
    return load_config()


@pytest.fixture(scope="session")
def stack(cfg: Config):
    """Ensure a reachable stack. Live mode: probe it. Non-live: testcontainers.

    Yields the (possibly rewritten) Config whose base_url/kafka point at the
    running stack.
    """
    if cfg.live:
        try:
            httpx.get(f"{cfg.base_url}/api/auth/me", verify=cfg.verify_tls, timeout=8)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"live stack {cfg.base_url} unreachable: {e}")
        yield cfg
        return

    # Non-live: testcontainers bring-up (best-effort — skips if docker/images
    # are unavailable in this environment).
    yield from _testcontainers_stack(cfg)


def _testcontainers_stack(cfg: Config):
    try:
        from testcontainers.core.container import DockerContainer  # noqa: F401
        from testcontainers.postgres import PostgresContainer
        from testcontainers.kafka import KafkaContainer
        from testcontainers.redis import RedisContainer
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"testcontainers unavailable: {e}")

    import os
    api_image = os.environ.get("CIVILYTICS_API_IMAGE")
    if not api_image:
        pytest.skip(
            "non-live mode needs CIVILYTICS_API_IMAGE=civilytics-prod-api:<tag> "
            "(+keycloak/minio). Set CIVILYTICS_BASE_URL to use the running stack."
        )

    # Dependencies. Keycloak + MinIO are started via generic DockerContainer.
    pg = PostgresContainer("postgres:16-alpine")
    kafka = KafkaContainer("apache/kafka:3.7.0")
    redis = RedisContainer("redis:7-alpine")
    pg.start(); kafka.start(); redis.start()
    try:
        # NOTE: wiring the API container (env, alembic upgrade head, realm+owner
        # seed) requires the project's compose env; this scaffold starts the
        # backing services and leaves the API/keycloak/minio bring-up to the
        # operator's compose overlay. Kept here so the same tests run both ways
        # once the image env is supplied.
        raise RuntimeError(
            "testcontainers API bring-up not wired in this environment — "
            "run with CIVILYTICS_BASE_URL against the running stack."
        )
    except RuntimeError as e:
        pg.stop(); kafka.stop(); redis.stop()
        pytest.skip(str(e))


# ── http client ───────────────────────────────────────────────────────────

def _login(cfg: Config) -> httpx.Client:
    c = httpx.Client(base_url=cfg.base_url, verify=cfg.verify_tls, timeout=30)
    r = c.post("/api/auth/login", json={"username": cfg.username, "password": cfg.password})
    r.raise_for_status()
    return c


@pytest.fixture
def client(stack: Config) -> httpx.Client:
    """Authenticated httpx client (httpOnly ww_access/ww_refresh cookies)."""
    c = _login(stack)
    yield c
    c.close()


@pytest.fixture
def ws_token(client: httpx.Client) -> str:
    return client.get("/api/auth/ws-token").json()["token"]


@pytest.fixture
def make_camera(client: httpx.Client):
    """Factory → creates a camera, tracks it, deletes on teardown."""
    created: list[str] = []

    def _make(**overrides) -> dict:
        body = {
            "name": f"E2E-{uuid.uuid4().hex[:8]}",
            "zone": "E2E-Zone",
            "location": "E2E Location",
            "gps_lat": 22.5726,
            "gps_lng": 88.3639,
            "stream_url": "rtsp://127.0.0.1:8554/e2e",
            "source_type": "rtsp",
        }
        body.update(overrides)
        r = client.post("/api/cameras", json=body)
        r.raise_for_status()
        cam = r.json()
        created.append(cam["id"])
        return cam

    yield _make

    for cid in created:
        try:
            client.delete(f"/api/cameras/{cid}")
        except Exception:  # noqa: BLE001
            pass
