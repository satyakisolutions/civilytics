# pipeline/app/confirmation.py
"""N-of-M rolling confirmation for a detection candidate.

Replaces a bare consecutive-frame counter (WASTE_CONFIRM_FRAMES=1, i.e. no
confirmation at all -- a single misclassified frame could kick off the full
alert flow). A RollingConfirmation requires N hits within the last M
observations (a bounded deque) before treating the candidate as confirmed,
and is edge-triggered so a single instance only ever fires once.
"""
from collections import deque


class RollingConfirmation:
    """Tracks a bounded window of hit/miss observations for one candidate.

    Usage:
        rc = RollingConfirmation(n=3, m=5)
        rc.observe(True)   # detector-confirmed on this eligible frame
        rc.observe(False)  # candidate absent on this eligible frame
        if rc.is_confirmed() and not rc.already_fired:
            rc.mark_fired()
            ...fire once...
    """

    __slots__ = ("n", "m", "_window", "already_fired")

    def __init__(self, n: int, m: int):
        if not (1 <= n <= m):
            raise ValueError(f"RollingConfirmation requires 1 <= n <= m, got n={n} m={m}")
        self.n = n
        self.m = m
        self._window: deque[bool] = deque(maxlen=m)
        self.already_fired = False

    def observe(self, hit: bool) -> None:
        """Record one eligible-inference-frame observation (hit or miss).

        Only call this for frames where the candidate was actually eligible
        for detection (e.g. skip frames dropped by an inference-interval
        setting) -- an ineligible frame is neither a hit nor a miss.
        """
        self._window.append(bool(hit))

    def is_confirmed(self) -> bool:
        return len(self._window) >= self.m and sum(self._window) >= self.n

    def mark_fired(self) -> None:
        self.already_fired = True

    def should_fire(self) -> bool:
        """True exactly once: confirmed, and not already fired."""
        return self.is_confirmed() and not self.already_fired

    def __len__(self) -> int:
        return len(self._window)
