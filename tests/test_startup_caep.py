"""ensure_caep_table must run while the connection it is given is still open.

Reads the source rather than importing app.main: the assertion is about
indentation and block membership, and importing the module drags in the whole
application just to look at four lines.
"""
import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
SRC = MAIN.read_text()

CAEP = "await ensure_caep_table(conn)"
SIBLING = "await ensure_reseller_admin_tables(conn)"


def _indent_of(needle: str) -> int:
    idx = SRC.index(needle)
    return idx - (SRC.rindex("\n", 0, idx) + 1)


def test_caep_is_indented_like_its_siblings():
    """It sat one level out, so every boot logged

        Billing tables warning: cannot call Connection.execute():
        connection has been released back to the pool

    and the CAEP table was never created. The surrounding `try` swallowed it,
    so the only symptom was a line in a startup log nobody read.
    """
    assert _indent_of(CAEP) == _indent_of(SIBLING), (
        f"ensure_caep_table indented {_indent_of(CAEP)}, siblings {_indent_of(SIBLING)} — "
        "it is outside the async with db_pool.acquire() block again"
    )


def test_caep_runs_before_the_block_reports_ready():
    assert SRC.index(CAEP) < SRC.index('print("Billing tables ready")')


def test_caep_is_in_the_acquire_body_that_opens_the_connection():
    """Walk the AST rather than trust the text."""
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
        if "ensure_caep_table" in body and "ensure_billing_tables" in body:
            return
    raise AssertionError("ensure_caep_table is not inside the acquire() body that opens it")
