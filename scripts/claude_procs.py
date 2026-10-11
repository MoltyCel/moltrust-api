"""Running Claude processes on the server, for the 08:00 report (since 2026-10-11).

From 01.10. to 11.10.2026 a Claude Code session ran in a tmux session started
by claude-session.service, with the deployed checkout ~/moltstack as its
working directory. Nobody noticed for ten days. This line counts every running
Claude process with its start and working directory, so such a session shows
on its first morning.

A process counts when its executable name or its first argument is "claude",
or its command line runs the @anthropic-ai/claude-code package.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess

MAX_ITEMS = 6


def _is_claude(argv: list[str], comm: str) -> bool:
    if comm == "claude":
        return True
    if argv and os.path.basename(argv[0]) == "claude":
        return True
    return any("@anthropic-ai/claude-code" in a or a.endswith("/bin/claude") for a in argv[:3])


def processes(proc: str = "/proc") -> list[tuple[int, str, str, dt.datetime | None]]:
    """(pid, cwd, command, start) per Claude process."""
    out = []
    try:
        boot = None
        with open(os.path.join(proc, "stat")) as fh:
            for ln in fh:
                if ln.startswith("btime "):
                    boot = int(ln.split()[1])
        hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    except OSError:
        boot, hz = None, 100
    for d in os.listdir(proc):
        if not d.isdigit():
            continue
        base = os.path.join(proc, d)
        try:
            with open(os.path.join(base, "cmdline"), "rb") as fh:
                argv = [a.decode(errors="replace") for a in fh.read().split(b"\0") if a]
            with open(os.path.join(base, "comm")) as fh:
                comm = fh.read().strip()
        except OSError:
            continue
        if not _is_claude(argv, comm):
            continue
        try:
            cwd = os.readlink(os.path.join(base, "cwd"))
        except OSError:
            cwd = "?"
        start = None
        try:
            with open(os.path.join(base, "stat")) as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
            if boot is not None:
                start = dt.datetime.fromtimestamp(boot + int(fields[19]) / hz, dt.timezone.utc)
        except (OSError, IndexError, ValueError):
            pass
        out.append((int(d), cwd, " ".join(argv)[:80], start))
    return sorted(out)


def lines(proc: str = "/proc") -> list[str]:
    try:
        ps = processes(proc)
    except OSError as exc:
        return [f"Claude-Prozesse nicht lesbar: {type(exc).__name__}"]
    head = f"Claude-Prozesse auf dem Server: {len(ps)}"
    # The unit that started the ten-day session. Enabled, it starts a new one in
    # the deployed checkout on the next boot, with nothing running before that.
    try:
        unit = subprocess.run(["systemctl", "is-enabled", "claude-session.service"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        unit = ""
    if unit == "enabled":
        head += " · claude-session.service aktiviert (startet beim Neustart eine Sitzung)"
    items = [f"  pid {pid} seit {start:%d.%m. %H:%M}Z in {cwd}" if start else f"  pid {pid} in {cwd}"
             for pid, cwd, _cmd, start in ps[:MAX_ITEMS]]
    return [head] + items


if __name__ == "__main__":
    print("\n".join(lines()))
