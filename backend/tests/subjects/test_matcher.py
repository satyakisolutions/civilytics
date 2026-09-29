import uuid

import pytest

from app.subjects.matcher import Hit, best_per_subject, decide, faces_over_cap

A, B = uuid.uuid4(), uuid.uuid4()


def test_best_per_subject_takes_max():
    hits = [Hit(A, uuid.uuid4(), 0.3), Hit(A, uuid.uuid4(), 0.7), Hit(B, uuid.uuid4(), 0.5)]
    assert best_per_subject(hits) == {A: 0.7, B: 0.5}


def test_decide_no_candidates_is_new():
    d = decide({}, 0.55, 0.40)
    assert (d.kind, d.subject_id, d.score) == ("new", None, None)


@pytest.mark.parametrize("score,kind", [
    (0.3999, "new"), (0.40, "review"), (0.5499, "review"), (0.55, "auto"), (0.99, "auto"),
])
def test_decide_threshold_boundaries(score, kind):
    d = decide({A: score}, 0.55, 0.40)
    assert d.kind == kind
    assert d.score == score
    assert d.subject_id == (None if kind == "new" else A)


def test_decide_picks_best_subject():
    d = decide({A: 0.6, B: 0.9}, 0.55, 0.40)
    assert (d.kind, d.subject_id) == ("auto", B)


def test_faces_over_cap_keeps_highest_quality():
    f1, f2, f3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert faces_over_cap([(f1, 0.1), (f2, 0.9), (f3, 0.5)], 2) == [f1]
    assert faces_over_cap([(f1, 0.1)], 2) == []
