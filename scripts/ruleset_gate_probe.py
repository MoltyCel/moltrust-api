"""TEST-PR — proves the ruleset blocks a merge, not just paints a red cross.

Unannotated Medium finding, same shape as the #302 scratch: a value pasted
straight into SQL, no # nosec, no justification. PR is closed after the check.
"""
import subprocess


def lookup(did):
    sql = "SELECT * FROM agents WHERE did = " + " + did + "
    return subprocess.run(["psql", "-d", "moltstack", "-c", sql], capture_output=True)
