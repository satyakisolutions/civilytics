import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.models import SubjectIncident
from app.subjects.worker import SubjectWorker, _backoff

from tests.subjects.factories import T0, face, unit


class FakeAnalyzer:
    def __init__(self, faces=None, fail_times: int = 0, fail_exc: Exception | None = None):
        self.faces = faces or []
        self.fail_times = fail_times
        self.fail_exc = fail_exc if fail_exc is not None else RuntimeError("model crashed")
        self.calls: list[list[str]] = []

    def __call__(self, paths):
        self.calls.append(list(paths))
        if self.fail_times:
            self.fail_times -= 1
            raise self.fail_exc
        return list(self.faces) if paths else []


class FakeMsg:
    def __init__(self, value):
        self.value = value


class FakeConsumer:
    def __init__(self, values, fail_commits: int = 0):
        self._values = values
        self.commits = 0
        self.fail_commits = fail_commits

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for v in self._values:
            yield FakeMsg(v)

    async def commit(self):
        self.commits += 1
        if self.fail_commits:
            self.fail_commits -= 1
            raise RuntimeError("CommitFailedError: rebalanced after max_poll_interval_ms")


@pytest.fixture
def make_worker(service, settings):
    def _make(analyzer):
        return SubjectWorker("kafka:9092", service, analyzer, settings)
    return _make


def _payload(settings, incident_id, n_crops=1, crops=None):
    folder = Path(settings.faces_dir) / "incoming" / str(incident_id)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n_crops):
        p = folder / f"{i:02d}.jpg"
        p.write_bytes(b"x")
        paths.append(str(p))
    return {
        "incident_id": str(incident_id), "camera_id": "cam-01", "detected_at": T0.timestamp(),
        "person_track_id": 7, "crops": paths if crops is None else crops,
    }


async def test_handle_runs_analyzer_on_own_crops_and_matches(make_worker, settings):
    analyzer = FakeAnalyzer([face(unit(1))])
    worker = make_worker(analyzer)
    iid = uuid.uuid4()
    payload = _payload(settings, iid, n_crops=2)
    result = await worker.handle(payload)
    assert (result.status, result.subject_code) == ("matched", "S-001")
    assert analyzer.calls == [[str(Path(p).resolve()) for p in payload["crops"]]]
    assert not (Path(settings.faces_dir) / "incoming" / str(iid)).exists()


async def test_handle_ignores_traversal_paths(make_worker, settings, tmp_path):
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"do not touch")
    analyzer = FakeAnalyzer([face(unit(1))])
    worker = make_worker(analyzer)
    iid = uuid.uuid4()
    sneaky = Path(settings.faces_dir) / "incoming" / str(iid) / ".." / ".." / ".." / "secret.jpg"
    result = await worker.handle(_payload(settings, iid, n_crops=0, crops=[str(secret), str(sneaky)]))
    assert analyzer.calls == [[]]
    assert result.status == "no_face"
    assert secret.read_bytes() == b"do not touch"


async def test_handle_with_retry_skips_malformed(make_worker):
    worker = make_worker(FakeAnalyzer())
    assert await worker.handle_with_retry({"camera_id": "cam-01"}) is None
    assert await worker.handle_with_retry(None) is None
    assert await worker.handle_with_retry({"incident_id": "nope"}) is None


async def test_handle_with_retry_retries_transient_errors(make_worker, settings):
    analyzer = FakeAnalyzer([face(unit(1))], fail_times=2)
    worker = make_worker(analyzer)
    result = await worker.handle_with_retry(_payload(settings, uuid.uuid4()), backoff_s=0)
    assert result.status == "matched"
    assert len(analyzer.calls) == 3


async def test_handle_with_retry_gives_up_after_attempts(make_worker, settings):
    analyzer = FakeAnalyzer([face(unit(1))], fail_times=5)
    worker = make_worker(analyzer)
    assert await worker.handle_with_retry(_payload(settings, uuid.uuid4()), attempts=3, backoff_s=0) is None
    assert len(analyzer.calls) == 3


async def test_handle_with_retry_retries_infra_errors_without_bound(make_worker, settings):
    analyzer = FakeAnalyzer(
        [face(unit(1))], fail_times=5,
        fail_exc=OperationalError("stmt", {}, Exception("db down")),
    )
    worker = make_worker(analyzer)
    result = await worker.handle_with_retry(_payload(settings, uuid.uuid4()), attempts=1, backoff_s=0)
    assert result.status == "matched"
    assert len(analyzer.calls) == 6


async def test_consume_loop_commits_after_each_message(make_worker, settings, session_factory):
    worker = make_worker(FakeAnalyzer([face(unit(1))]))
    payload = _payload(settings, uuid.uuid4())
    worker._consumer = FakeConsumer([payload, dict(payload), {"bad": 1}])  # redelivery + malformed
    await worker._consume_loop()
    assert worker._consumer.commits == 3
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(SubjectIncident))).scalar_one() == 1


async def test_consume_loop_survives_commit_failure(make_worker, settings, session_factory):
    worker = make_worker(FakeAnalyzer([face(unit(1))]))
    first, second = _payload(settings, uuid.uuid4()), _payload(settings, uuid.uuid4())
    worker._consumer = FakeConsumer([first, second], fail_commits=1)
    await worker._consume_loop()
    assert worker._consumer.commits == 2
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(SubjectIncident))).scalar_one() == 2


def test_backoff_is_capped_and_never_overflows():
    assert _backoff(1.0, 30.0, 1) == 2.0
    assert _backoff(1.0, 30.0, 3) == 8.0
    assert _backoff(1.0, 30.0, 10) == 30.0
    assert _backoff(1.0, 30.0, 5000) == 30.0
    assert _backoff(0.0, 30.0, 5000) == 0.0


async def test_handle_with_retry_integrity_error_is_bounded(make_worker, settings):
    analyzer = FakeAnalyzer(
        [face(unit(1))], fail_times=5,
        fail_exc=IntegrityError("stmt", {}, Exception("duplicate key")),
    )
    worker = make_worker(analyzer)
    assert await worker.handle_with_retry(_payload(settings, uuid.uuid4()), attempts=3, backoff_s=0) is None
    assert len(analyzer.calls) == 3


async def test_handle_with_retry_retries_connection_errors_without_bound(make_worker, settings):
    analyzer = FakeAnalyzer([face(unit(1))], fail_times=4, fail_exc=ConnectionRefusedError("qdrant down"))
    worker = make_worker(analyzer)
    result = await worker.handle_with_retry(_payload(settings, uuid.uuid4()), attempts=1, backoff_s=0)
    assert result.status == "matched"
    assert len(analyzer.calls) == 5


class CrashingConsumer:
    """aiokafka stand-in whose iteration dies (e.g. broker gone for good)."""

    def __init__(self, *args, **kwargs):
        self.stopped = False

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError("consumer died")


class IdleConsumer(CrashingConsumer):
    async def __anext__(self):
        await asyncio.sleep(3600)


class NullService:
    """No-DB service so the maintenance task never leaves a query in flight."""

    async def resync_index(self):
        return 0

    async def reconcile(self):
        return None

    async def sweep(self):
        return None


async def test_crashed_consume_task_sets_failure_event(settings):
    worker = SubjectWorker("kafka:9092", NullService(), FakeAnalyzer(), settings, consumer_factory=CrashingConsumer)
    await worker.start()
    await asyncio.wait_for(worker.wait_failed(), timeout=2)
    assert worker.failed is True
    await worker.stop()


async def test_cancelled_tasks_do_not_set_failure_event(settings):
    worker = SubjectWorker("kafka:9092", NullService(), FakeAnalyzer(), settings, consumer_factory=IdleConsumer)
    await worker.start()
    await asyncio.sleep(0)
    await worker.stop()
    assert worker.failed is False


async def test_entrypoint_supervise_exit_codes(settings):
    from services.subjects.main import supervise

    crashing = SubjectWorker("kafka:9092", NullService(), FakeAnalyzer(), settings, consumer_factory=CrashingConsumer)
    await crashing.start()
    assert await asyncio.wait_for(supervise(crashing, asyncio.Event()), timeout=2) == 1

    idle = SubjectWorker("kafka:9092", NullService(), FakeAnalyzer(), settings, consumer_factory=IdleConsumer)
    await idle.start()
    shutdown = asyncio.Event()
    shutdown.set()
    assert await asyncio.wait_for(supervise(idle, shutdown), timeout=2) == 0


class RecordingService:
    """Maintenance-loop stand-in: resync fails, the other steps must still run."""

    def __init__(self):
        self.calls: list[str] = []

    async def resync_index(self):
        self.calls.append("resync")
        raise ConnectionError("qdrant down")

    async def reconcile(self):
        self.calls.append("reconcile")
        raise RuntimeError("reconcile failed")

    async def sweep(self):
        self.calls.append("sweep")


async def test_maintenance_runs_reconcile_after_resync_each_isolated(settings):
    service = RecordingService()
    worker = SubjectWorker("kafka:9092", service, FakeAnalyzer(), settings, resync_every_s=3600)
    task = asyncio.create_task(worker._maintenance_loop())
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert service.calls == ["resync", "reconcile", "sweep"]


def test_service_entrypoint_imports():
    from services.subjects import main as entry

    assert callable(entry.main)
