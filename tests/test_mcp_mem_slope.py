"""c-mcp-mem-slope: steep warns, the measured rate stays silent, a restart resets."""
from scripts.mcp_mem_slope import slope

H = 3600.0


def _series(pid, start_mb, mb_per_h, hours, t0=0.0):
    return [{"t": t0 + h * H, "pid": pid, "mem": int((start_mb + mb_per_h * h) * 1e6)}
            for h in range(0, hours + 1, 24)]


def test_measured_rate_stays_under_the_threshold():
    s = _series(1343252, 50.9, 0.33, 72)
    mb_h, _ = slope(s, s[-1]["t"])
    assert 0.3 < mb_h < 0.4 and mb_h <= 1.0


def test_a_steeper_curve_crosses_it():
    s = _series(1, 50, 3.0, 48)
    mb_h, _ = slope(s, s[-1]["t"])
    assert mb_h > 1.0


def test_a_restart_starts_a_new_series():
    old = _series(1, 50, 30.0, 48)
    new = [{"t": old[-1]["t"] + H, "pid": 2, "mem": 50_000_000}]
    mb_h, why = slope(old + new, new[-1]["t"])
    assert mb_h is None and "nicht messbar" in why


def test_samples_older_than_a_week_drop_out():
    old = _series(1, 50, 30.0, 48)
    recent = _series(1, 2000, 0.3, 48, t0=10 * 24 * H)
    mb_h, _ = slope(old + recent, recent[-1]["t"])
    assert mb_h < 1.0
