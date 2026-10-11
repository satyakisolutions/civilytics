import numpy as np
import pytest

from facelab.gallery import Gallery


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def with_similarity(base: np.ndarray, sim: float, seed: int) -> np.ndarray:
    u = unit(seed)
    u = u - float(np.dot(u, base)) * base
    u /= np.linalg.norm(u)
    v = sim * base + np.sqrt(1 - sim * sim) * u
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_first_visit_is_new_then_same_face_is_repeat():
    g = Gallery()
    v = unit(1)
    first = g.enroll([(0.8, v)], when=0.0)
    assert (first.kind, first.code, first.incident_count, first.similarity) == ("new", "S-001", 1, None)
    again = g.enroll([(0.8, with_similarity(v, 0.8, seed=2))], when=10.0)
    assert (again.kind, again.code, again.incident_count) == ("auto", "S-001", 2)
    assert again.similarity == pytest.approx(0.8, abs=1e-3)


def test_match_while_still_present_is_same_visit_not_repeat():
    g = Gallery()
    v = unit(1)
    g.enroll([(0.8, v)], when=10.0)
    g.touch(1, 12.0)
    same = g.enroll([(0.8, with_similarity(v, 0.8, 7))], when=13.0, visit_start=14.0, continuation_gap_s=3.0)
    assert (same.kind, same.code, same.incident_count) == ("same", "S-001", 1)
    later = g.enroll([(0.8, with_similarity(v, 0.8, 8))], when=30.0, visit_start=29.0, continuation_gap_s=3.0)
    assert (later.kind, later.incident_count) == ("auto", 2)


def test_uncertain_match_creates_new_subject_with_candidate():
    g = Gallery()
    v = unit(1)
    g.enroll([(0.8, v)], when=0.0)
    r = g.enroll([(0.8, with_similarity(v, 0.45, seed=3))], when=5.0)
    assert (r.kind, r.code, r.candidate_code) == ("review", "S-002", "S-001")


def test_different_person_is_new():
    g = Gallery()
    g.enroll([(0.8, unit(1))], when=0.0)
    r = g.enroll([(0.8, unit(99))], when=5.0)
    assert (r.kind, r.code, r.candidate_code) == ("new", "S-002", None)


def test_face_cap_keeps_best_quality():
    g = Gallery(max_faces=2)
    v = unit(1)
    g.enroll([(0.1, v), (0.9, with_similarity(v, 0.9, 4)), (0.5, with_similarity(v, 0.9, 5))], when=0.0)
    assert g.subjects[1].qualities == [0.9, 0.5]


def test_empty_samples_rejected():
    with pytest.raises(ValueError):
        Gallery().enroll([], when=0.0)


def test_save_and_load_roundtrip(tmp_path):
    g = Gallery()
    v = unit(1)
    g.enroll([(0.8, v)], when=1.0)
    g.enroll([(0.7, unit(50))], when=2.0)
    g.save(tmp_path)
    loaded = Gallery.load(tmp_path)
    assert sorted(loaded.subjects) == [1, 2]
    r = loaded.enroll([(0.8, with_similarity(v, 0.8, 6))], when=3.0)
    assert (r.kind, r.code, r.incident_count) == ("auto", "S-001", 2)
    assert Gallery.load(tmp_path / "missing").subjects == {}
