# backend/tests/test_capture_switch.py
"""The capture switch must be off unless someone deliberately turned it on."""
import pytest

from app.logs import capture_switch


class FakeRedis:
    def __init__(self, *, broken: bool = False):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.broken = broken

    def _check(self):
        if self.broken:
            raise ConnectionError("redis down")

    async def exists(self, key):
        self._check()
        return 1 if key in self.store else 0

    async def ttl(self, key):
        self._check()
        return self.ttls.get(key, -2)

    async def set(self, key, value, ex=None):
        self._check()
        self.store[key] = value
        self.ttls[key] = ex

    async def delete(self, key):
        self._check()
        self.store.pop(key, None)
        self.ttls.pop(key, None)

    async def get(self, key):
        self._check()
        return self.store.get(key)


@pytest.fixture
def fake(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(capture_switch, "_redis", lambda: r)
    return r


async def test_off_by_default(fake):
    assert await capture_switch.is_enabled() is False
    assert await capture_switch.remaining_s() == 0


async def test_enable_then_disable(fake):
    await capture_switch.enable(ttl_s=600, enabled_by="admin1")
    assert await capture_switch.is_enabled() is True
    assert await capture_switch.enabled_by() == "admin1"
    await capture_switch.disable(disabled_by="admin1")
    assert await capture_switch.is_enabled() is False


async def test_window_always_has_a_ttl(fake):
    """A forgotten "on" has to expire by itself -- that is the whole point."""
    await capture_switch.enable(ttl_s=600, enabled_by="admin1")
    assert fake.ttls[ "ww:logs:client_capture" ] == 600


async def test_ttl_clamped_to_a_day(fake):
    applied = await capture_switch.enable(ttl_s=999_999, enabled_by="admin1")
    assert applied == capture_switch.MAX_TTL_S


async def test_ttl_has_a_floor(fake):
    applied = await capture_switch.enable(ttl_s=1, enabled_by="admin1")
    assert applied == 60


async def test_fails_closed_when_redis_is_down(monkeypatch):
    """A transport fault must not be a way to start recording every
    operator's browser. Unreadable switch == off."""
    monkeypatch.setattr(capture_switch, "_redis", lambda: FakeRedis(broken=True))
    assert await capture_switch.is_enabled() is False
    assert await capture_switch.remaining_s() == 0
    assert await capture_switch.enabled_by() is None
