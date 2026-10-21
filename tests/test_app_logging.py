"""The "moltrust" logger family reaches stderr, and with it the journal.

Before app/app_logging.py, INFO from "moltrust" never left the process in
production: uvicorn configures only its own loggers, the root stays at WARNING
without a handler, and "moltrust" had no level of its own.
"""
import io
import logging
import sys

import pytest

from app import app_logging


@pytest.fixture
def moltrust_logger():
    """The "moltrust" logger with its handlers and level restored afterwards."""
    logger = logging.getLogger("moltrust")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    yield logger
    logger.handlers[:] = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]


def _marked(logger):
    return [h for h in logger.handlers if getattr(h, app_logging._MARKER, False)]


def test_info_from_moltrust_reaches_a_stderr_handler_at_info(moltrust_logger, monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    handler = app_logging.configure()

    assert handler.level == logging.INFO
    assert moltrust_logger.getEffectiveLevel() == logging.INFO
    moltrust_logger.info("probe from the app logger")
    logging.getLogger("moltrust.ipfs").info("probe from a child")

    out = buf.getvalue()
    assert "INFO moltrust: probe from the app logger" in out
    assert "INFO moltrust.ipfs: probe from a child" in out


def test_debug_stays_out(moltrust_logger, monkeypatch):
    # The silence probe for the test above: INFO is the floor, not "everything".
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    app_logging.configure()
    moltrust_logger.debug("debug line that must not appear")
    assert buf.getvalue() == ""


def test_configure_is_idempotent_and_each_line_is_written_once(moltrust_logger, monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    first = app_logging.configure()
    second = app_logging.configure()
    assert first is second
    assert len(_marked(moltrust_logger)) == 1

    moltrust_logger.info("written once")
    assert buf.getvalue().count("written once") == 1


def test_app_main_configures_it_exactly_once():
    import app.main  # noqa: F401  (import is the point)

    assert len(_marked(logging.getLogger("moltrust"))) == 1
    assert logging.getLogger("moltrust").getEffectiveLevel() <= logging.INFO


def test_other_libraries_keep_their_levels(moltrust_logger):
    names = ("httpx", "httpcore", "asyncpg", "uvicorn", "uvicorn.access", "")
    before = {n: logging.getLogger(n).level for n in names}
    app_logging.configure()
    assert {n: logging.getLogger(n).level for n in names} == before


def test_uvicorn_default_config_leaves_root_and_moltrust_alone():
    # The no-duplicate premise: propagate stays on (caplog needs it), which is
    # only safe while uvicorn puts no handler on the root or on "moltrust".
    from uvicorn.config import LOGGING_CONFIG

    assert "root" not in LOGGING_CONFIG
    assert "" not in LOGGING_CONFIG.get("loggers", {})
    assert not any(n == "moltrust" or n.startswith("moltrust.")
                   for n in LOGGING_CONFIG.get("loggers", {}))
