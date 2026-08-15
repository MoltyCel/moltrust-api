"""SCRATCH — proves the hardened bandit step can fail. Reverted next commit.

An unannotated Medium finding: SQL built by pasting a value straight into the
statement, with no # nosec and no justification. Exactly what the gate is for.
"""
import subprocess


def lookup(did):
    sql = "SELECT * FROM agents WHERE did = " + " + did + "
    return subprocess.run(["psql", "-d", "moltstack", "-c", sql], capture_output=True)
