import numpy as np

from facelab.faces import FaceObs
from facelab.gallery import Gallery
from facelab.session import Session, iou


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def face(x: float, emb: np.ndarray | None, reason: str | None = None, quality: float = 0.8) -> FaceObs:
    return FaceObs(
        bbox=np.array([x, 100.0, x + 80.0, 180.0]), det_score=0.9, width=80.0, sharpness=150.0, yaw=0.0,
        reason=reason, quality=quality if reason is None else 0.0,
        embedding=emb if reason is None else None, aligned=np.zeros((112, 112, 3), np.uint8),
    )


def run(session, frames):
    for t, faces in frames:
        session.step(faces, t)


def test_iou():
    a = np.array([0, 0, 10, 10.0])
    assert iou(a, a) == 1.0
    assert iou(a, np.array([20, 20, 30, 30.0])) == 0.0


def test_visit_matched_once_after_enough_samples_then_repeat_on_return():
    results = []
    s = Session(Gallery(), need_samples=3, gone_after_s=3.0, on_result=results.append)
    v = unit(1)
    run(s, [(0.0, [face(100, v)]), (0.2, [face(104, v)]), (0.4, [face(108, v)])])
    assert [(r.status, r.result.code) for r in results] == [("new", "S-001")]
    run(s, [(0.6, [face(110, v)]), (0.8, [face(112, v)])])   # still same visit: no new result
    assert len(results) == 1
    run(s, [(5.0, [])])                                      # gone > 3 s: visit closes
    run(s, [(6.0, [face(300, v)]), (6.2, [face(302, v)]), (6.4, [face(304, v)])])
    assert [(r.status, r.result.code, r.result.incident_count) for r in results][-1] == ("repeat", "S-001", 2)


def test_two_people_in_view_get_two_ids():
    results = []
    s = Session(Gallery(), need_samples=1, on_result=results.append)
    s.step([face(100, unit(1)), face(500, unit(2))], 0.0)
    assert sorted(r.result.code for r in results) == ["S-001", "S-002"]


def test_low_quality_only_visit_reports_face_not_captured():
    results = []
    s = Session(Gallery(), need_samples=3, gone_after_s=1.0, min_no_face_s=1.0, on_result=results.append)
    run(s, [(t / 5, [face(100, None, reason="blurry")]) for t in range(8)])   # 1.4 s of blurry faces
    run(s, [(4.0, [])])
    assert [r.status for r in results] == ["no_face"]


def test_brief_false_detection_is_not_reported():
    results = []
    s = Session(Gallery(), gone_after_s=1.0, min_no_face_s=1.0, on_result=results.append)
    run(s, [(0.0, [face(100, None, reason="too small")]), (3.0, [])])
    assert results == []


def test_visit_matched_after_collect_window_with_fewer_samples():
    results = []
    s = Session(Gallery(), need_samples=5, collect_s=2.0, on_result=results.append)
    run(s, [(0.0, [face(100, unit(1))]), (1.0, [face(102, None, reason="turned away")]), (2.1, [face(104, None, reason="blurry")])])
    assert [r.status for r in results] == ["new"]


def test_finish_matches_open_visits():
    results = []
    s = Session(Gallery(), need_samples=5, collect_s=10.0, on_result=results.append)
    s.step([face(100, unit(1))], 0.0)
    s.finish(0.5)
    assert [r.status for r in results] == ["new"]
