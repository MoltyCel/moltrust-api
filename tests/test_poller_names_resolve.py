"""Every global name the USDC poller's functions use exists (ops proposal C3, 2026-10-12).

#689 removed `record_to_db` from monitor/poll_payments.py while main() still
called it. Nothing noticed: the call only runs when a transfer is found, and the
first transfer after 2026-10-09 (0.39 USDC, block 52520458) ended every hourly
run in a NameError and held the cursor. This checks the compiled functions,
not a run, so it fails on the missing name whether or not a transfer exists.
"""
import builtins
import importlib
import sys
import types


def _names(code):
    out = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            out |= _names(const)
    return out


def test_every_global_a_poller_function_reads_is_defined(monkeypatch, tmp_path):
    (tmp_path / ".moltrust_secrets").write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delitem(sys.modules, "monitor.poll_payments", raising=False)
    poller = importlib.import_module("monitor.poll_payments")
    known = set(vars(poller)) | set(dir(builtins))
    missing = {}
    for name, obj in vars(poller).items():
        if isinstance(obj, types.FunctionType) and obj.__module__ == poller.__name__:
            # co_names mixes globals and attribute names; an attribute name that
            # is also not a global would be a false positive, so only names that
            # the function loads as globals are checked.
            import dis
            globals_read = {i.argval for i in dis.get_instructions(obj)
                            if i.opname in ("LOAD_GLOBAL", "LOAD_NAME")}
            unknown = globals_read - known
            if unknown:
                missing[name] = sorted(unknown)
    assert missing == {}, missing


def test_record_to_db_exists():
    import monitor.poll_payments as poller
    assert callable(getattr(poller, "record_to_db", None))
