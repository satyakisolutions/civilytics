"""Tests for RollingConfirmation (N-of-M waste confirmation)."""
import pytest
from app.confirmation import RollingConfirmation


def _feed(rc: RollingConfirmation, sequence: list[int]) -> RollingConfirmation:
    for hit in sequence:
        rc.observe(bool(hit))
    return rc


def test_all_miss_not_confirmed():
    rc = _feed(RollingConfirmation(n=3, m=5), [1, 0, 0, 0, 0])
    assert not rc.is_confirmed()


def test_three_of_five_scattered_confirmed():
    rc = _feed(RollingConfirmation(n=3, m=5), [1, 1, 0, 1, 0])
    assert rc.is_confirmed()


def test_three_of_five_alternating_confirmed():
    rc = _feed(RollingConfirmation(n=3, m=5), [1, 0, 1, 0, 1])
    assert rc.is_confirmed()


def test_two_of_five_not_confirmed():
    rc = _feed(RollingConfirmation(n=3, m=5), [1, 1, 0, 0, 0])
    assert not rc.is_confirmed()


def test_not_confirmed_before_window_fills():
    rc = RollingConfirmation(n=1, m=5)
    rc.observe(True)
    # Only 1 observation so far even though n=1 -- window must reach m first.
    assert not rc.is_confirmed()


def test_window_is_bounded_old_hits_fall_off():
    rc = RollingConfirmation(n=3, m=5)
    _feed(rc, [1, 1, 1, 0, 0])
    assert rc.is_confirmed()
    # Pushes the three old hits out one at a time.
    rc.observe(False)
    rc.observe(False)
    rc.observe(False)
    assert not rc.is_confirmed()


def test_should_fire_is_edge_triggered_once():
    rc = _feed(RollingConfirmation(n=3, m=5), [1, 1, 1, 0, 0])
    assert rc.should_fire()
    rc.mark_fired()
    assert not rc.should_fire()
    # Still confirmed on subsequent hits, but never fires again.
    rc.observe(True)
    assert rc.is_confirmed()
    assert not rc.should_fire()


def test_invalid_n_m_rejected():
    with pytest.raises(ValueError):
        RollingConfirmation(n=0, m=5)
    with pytest.raises(ValueError):
        RollingConfirmation(n=6, m=5)


def test_len_reflects_window_size_capped_at_m():
    rc = RollingConfirmation(n=1, m=3)
    for _ in range(10):
        rc.observe(True)
    assert len(rc) == 3
