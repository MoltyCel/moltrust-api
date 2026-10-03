#!/usr/bin/env python3
"""Every sudo call in a cron-scheduled script against what sudo -n -l permits.

The question is not "may moltstack run this" — the first sudoers rule is
`(ALL : ALL) ALL`, so the answer is always yes, with a password, from a
terminal nobody is sitting at. The question is whether it runs *without* a
password, because a cron job has no way to supply one. So only the NOPASSWD
specs count.

Comparing the binary alone is not enough either, and that is how the first
version of this check reported green on the defect it was written for:
`/usr/bin/install` is in the NOPASSWD list, but only with one exact argument
list, for the agent card. The script's own `install -m 644 … registry-proof.json`
shares nothing with it but the path.

Shell variables in a script's sudo line cannot be resolved statically, so they
are matched as wildcards. That direction is deliberate: a wildcard can turn a
real finding into a pass, never a pass into a false finding, and a check that
cries wolf gets switched off.
"""
import fnmatch
import os
import re
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")
SUDO_OPTS_WITH_ARG = {"-u", "-g", "-U", "-C", "-p", "-r", "-t", "-T", "-h"}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def nopasswd_specs():
    """The NOPASSWD command specs, or None if sudo -n -l did not answer."""
    p = run(["sudo", "-n", "-l"])
    if p.returncode != 0 or "may run the following" not in p.stdout:
        return None
    specs, carry = [], False
    for line in p.stdout.splitlines():
        if not line.startswith((" ", "\t")):
            carry = False
            continue
        stripped = line.strip()
        if "NOPASSWD:" in stripped:
            # A tag list may hold more than NOPASSWD; take what follows the last one.
            specs += [s.strip() for s in stripped.split("NOPASSWD:")[-1].split(",")]
            carry = True
        elif carry and not stripped.startswith("("):
            # Continuation of the previous, still-NOPASSWD spec list.
            specs += [s.strip() for s in stripped.split(",")]
        else:
            carry = False
    return [s for s in specs if s]


def cron_scripts():
    """Shell scripts named by the user's crontab, as absolute paths."""
    p = run(["crontab", "-l"])
    if p.returncode != 0:
        return None
    out = []
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for m in re.finditer(r"(?:~|/home/[A-Za-z0-9._-]+)[A-Za-z0-9._/-]*\.sh", line):
            path = m.group(0)
            if path.startswith("~"):
                path = HOME + path[1:]
            out.append(path)
    return sorted(set(out))


def sudo_calls(path):
    """The sudo invocations in one script, as (line number, command) pairs."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    calls = []
    for n, line in enumerate(lines, 1):
        code = line.split("#", 1)[0]
        for m in re.finditer(r"(?<![\w/-])sudo\b(.*)", code):
            rest = m.group(1)
            # One command per call: stop at a shell operator.
            rest = re.split(r"[|;&><)]|\$\(", rest, maxsplit=1)[0].strip()
            if rest:
                calls.append((n, rest))
    return calls


def resolve(binary):
    """One spelling per binary on both sides of the comparison.

    `/bin` is a symlink to `usr/bin` here, so `monitor.sh` calling
    `sudo systemctl` and a sudoers rule reading `/bin/systemctl` are the same
    command. sudo knows that; a string comparison does not, and reported the
    restart as forbidden on the first run.
    """
    if not binary.startswith("/"):
        binary = shutil.which(binary) or binary
    try:
        return os.path.realpath(binary)
    except OSError:
        return binary


def normalise(cmd):
    """Strip sudo's own options, resolve the binary, blank out variables."""
    toks, i = cmd.split(), 0
    while i < len(toks):
        t = toks[i]
        if t in SUDO_OPTS_WITH_ARG:
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            break
    toks = toks[i:]
    if not toks:
        return None
    binary = resolve(toks[0])
    args = []
    for t in toks[1:]:
        t = t.strip("\"'")
        # Anything a variable or a substitution reaches is unknowable here.
        args.append("*" if ("$" in t or "`" in t) else t)
    return binary, args


def permitted(binary, args, specs):
    for spec in specs:
        parts = spec.split()
        if not parts:
            continue
        if parts[0] != "ALL" and resolve(parts[0]) != binary:
            continue
        # A sudoers command without an argument list permits any arguments.
        if len(parts) == 1:
            return spec
        if fnmatch.fnmatch(" ".join(args), " ".join(parts[1:])):
            return spec
    return None


def main():
    specs = nopasswd_specs()
    if specs is None:
        print("UNREADABLE: sudo -n -l gave no command list", file=sys.stderr)
        print(-1)
        return 2
    scripts = cron_scripts()
    if scripts is None:
        print("UNREADABLE: crontab -l failed", file=sys.stderr)
        print(-1)
        return 2
    if not scripts:
        print("UNREADABLE: the crontab names no shell script", file=sys.stderr)
        print(-1)
        return 2

    findings, checked = [], 0
    for path in scripts:
        if not os.path.isfile(path):
            continue  # c-cron-targets-exist owns that question
        calls = sudo_calls(path)
        if calls is None:
            findings.append(f"UNREADABLE {path}")
            continue
        for lineno, raw in calls:
            norm = normalise(raw)
            if norm is None:
                continue
            checked += 1
            binary, args = norm
            if not permitted(binary, args, specs):
                findings.append(
                    f"{path}:{lineno}: not permitted without a password: "
                    f"{binary} {' '.join(args)}".rstrip()
                )

    for f in findings:
        print(f, file=sys.stderr)
    print(f"{len(scripts)} scripts, {checked} sudo calls checked", file=sys.stderr)
    print(len(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
