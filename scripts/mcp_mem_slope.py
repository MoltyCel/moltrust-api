"""Memory slope of moltrust-mcp-http, in MB per hour, for c-mcp-mem-slope.

Each run appends one sample (time, PID, cgroup MemoryCurrent) to
~/selftest/mcp_mem.jsonl and prints the slope across the samples of the running
process over the last seven days. The last line is the number the invariant
judges; the lines above it say what it rests on.

Measured 09.-11.10.2026 on PID 1343252: 50.9 -> 65.3 MB cgroup over 40.7 h,
about 0.33 MB/h and linear, against the 30 MB/h the drop-in was planned for.
The invariant warns from 1.0 MB/h, three times the measured rate: steeper
than now, and still far below a leak that fills the host in days.

A restart starts a new series. Under SPAN_MIN_H of samples the slope is not
measurable; the run says so and prints 0, so a fresh restart stays silent and
its reason is written down.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

UNIT = "moltrust-mcp-http.service"
LEDGER = os.path.expanduser("~/selftest/mcp_mem.jsonl")
WINDOW_H = 7 * 24
SPAN_MIN_H = 12


def sample() -> dict:
    out = subprocess.run(["systemctl", "show", UNIT, "-p", "MainPID", "-p", "MemoryCurrent"],
                         capture_output=True, text=True, check=True).stdout
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {"t": time.time(), "pid": int(kv["MainPID"]), "mem": int(kv["MemoryCurrent"])}


def slope(samples: list[dict], now: float) -> tuple[float | None, str]:
    """MB/h across the current PID's samples in the window, or None with a reason."""
    if not samples:
        return None, "keine Messwerte"
    pid = samples[-1]["pid"]
    if pid == 0:
        return None, "Dienst laeuft nicht"
    run = [s for s in samples if s["pid"] == pid and now - s["t"] <= WINDOW_H * 3600]
    span_h = (run[-1]["t"] - run[0]["t"]) / 3600
    if span_h < SPAN_MIN_H:
        return None, f"PID {pid}: {span_h:.1f} h Messreihe, unter {SPAN_MIN_H} h nicht messbar"
    mb_h = (run[-1]["mem"] - run[0]["mem"]) / 1e6 / span_h
    return mb_h, (f"PID {pid}: {run[0]['mem'] / 1e6:.1f} -> {run[-1]['mem'] / 1e6:.1f} MB "
                  f"ueber {span_h:.1f} h, {len(run)} Messwerte")


def main() -> int:
    s = sample()
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(s) + "\n")
    with open(LEDGER) as f:
        samples = [json.loads(line) for line in f if line.strip()]
    mb_h, why = slope(samples, s["t"])
    print(why)
    print(f"{mb_h:.2f}" if mb_h is not None else "0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
