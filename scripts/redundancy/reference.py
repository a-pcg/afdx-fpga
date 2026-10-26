"""Independent state specification; does not parse or import RTL.

Two dev-profile modes are modelled explicitly:
  expected_sn=False (default) : legacy acceptance-order RM (SKEW_MAX window duplicate test)
  expected_sn=True            : RM expected-SN arbitration (docs/rx/rm_expected_sn_design.md)
"""
class Reference:
    def __init__(self, skew=100, expected_sn=False, resync_timeout=1000000, gap_tolerance=2,
                 sn0_restart=True):
        self.observed = {}
        self.delivered = {}
        self.skew = skew
        self.expected_sn = expected_sn
        self.resync_timeout = resync_timeout
        self.gap_tolerance = gap_tolerance
        self.sn0_restart = sn0_restart

    def _abs_delta(self, a, b):
        d = (a - b) % (2 ** 32)
        return min(d, (2 ** 32) - d)

    def decide(self, net, vl, sn, good, enabled, stamp):
        old = self.observed.get((net, vl))
        if not good:
            return 0, 1
        # Nonzero sequence ring enumerated independently of RTL successor.
        ring = list(range(1, 256))
        allowed = old is None or sn == 0 or not enabled
        if old is not None:
            after = [1, 2] if old == 0 else [ring[(ring.index(old) + i) % 255] for i in (1, 2)]
            allowed |= sn in after
        self.observed[net, vl] = sn
        if not allowed:
            return 0, 2
        prior = self.delivered.get(vl)
        if self.expected_sn:
            if prior is None:
                self.delivered[vl] = sn, stamp
                return 1, 0
            old_sn, old_time = prior
            if sn == old_sn:
                return 0, 3
            delta = self._abs_delta(stamp, old_time)
            expected = (old_sn == 255) and 1 or (old_sn + 1)
            diff = (sn - expected) & 0xFF
            if sn == 0 and self.sn0_restart and delta > self.resync_timeout:
                self.delivered[vl] = sn, stamp
                return 1, 0
            if diff == 0 or 1 <= diff <= self.gap_tolerance:
                self.delivered[vl] = sn, stamp
                return 1, 0
            if diff >= 128:
                return 0, 4
            if delta > self.resync_timeout:
                self.delivered[vl] = sn, stamp
                return 1, 0
            return 0, 5
        if prior is not None:
            old_sn, old_time = prior
            a = (stamp - old_time) % (2**32)
            b = (old_time - stamp) % (2**32)
            if sn == old_sn and min(a, b) <= self.skew:
                return 0, 3
        self.delivered[vl] = sn, stamp
        return 1, 0


def anchors():
    # Literal expected outcomes prevent a self-consistent but incorrect generator.
    cases = [
        (0,0,254,1,1,0,1,0), (1,0,254,1,1,100,0,3),
        (0,0,255,1,1,101,1,0), (1,0,255,1,1,202,1,0),
        (0,0,1,1,1,203,1,0), (1,0,1,1,1,303,0,3),
        (0,0,0,1,1,304,1,0), (1,0,0,1,1,304,0,3),
        (0,0,77,0,1,305,0,1), (0,0,1,1,1,306,1,0),
        (0,0,9,1,1,307,0,2), (0,0,10,1,1,308,1,0),
        (1,1,66,1,1,0xfffffff0,1,0), (0,1,66,1,1,0x10,0,3),
        (0,2,6,1,1,500,1,0), (1,2,6,1,1,400,0,3),
        (0,3,5,0,1,1,0,1), (1,3,5,1,1,2,1,0),
        (0,4,0,1,1,1,1,0), (1,4,0,1,1,102,1,0),
        (0,5,9,1,0,1000,1,0), (1,5,9,1,0,1099,0,3),
        (0,5,9,1,0,1101,1,0),
    ]
    r = Reference()
    for *args, passed, reason in cases:
        assert r.decide(*args) == (passed, reason), (args, passed, reason)
    return cases


def anchors_on():
    # Same stimulus with the RM expected-SN profile (literal expectations):
    # duplicate = same SN regardless of the SFD window; SN0 restart only after a
    # silence longer than the resync timeout; late copies are reason 4; large
    # forward gaps are reason 5 unless the resync timeout has elapsed.
    cases = [
        (0,0,254,1,1,0,1,0), (1,0,254,1,1,100,0,3),
        (0,0,255,1,1,101,1,0), (1,0,255,1,1,202,0,3),
        (0,0,1,1,1,203,1,0), (1,0,1,1,1,303,0,3),
        (0,0,0,1,1,304,0,4), (1,0,0,1,1,304,0,4),
        (0,0,77,0,1,305,0,1), (0,0,1,1,1,306,0,3),
        (0,0,9,1,1,307,0,2), (0,0,10,1,1,308,0,5),
        (1,1,66,1,1,0xfffffff0,1,0), (0,1,66,1,1,0x10,0,3),
        (0,2,6,1,1,500,1,0), (1,2,6,1,1,400,0,3),
        (0,3,5,0,1,1,0,1), (1,3,5,1,1,2,1,0),
        (0,4,0,1,1,1,1,0), (1,4,0,1,1,102,0,3),
        (0,5,9,1,0,1000,1,0), (1,5,9,1,0,1099,0,3),
        (0,5,9,1,0,1101,0,3),
    ]
    r = Reference(expected_sn=True)
    for *args, passed, reason in cases:
        assert r.decide(*args) == (passed, reason), (args, passed, reason)
    return cases
