"""Make the "moltrust" logger family reach the journal.

Why this exists: the API runs as ``uvicorn app.main:app`` under systemd, and
uvicorn's default dictConfig configures only its own loggers (``uvicorn``,
``uvicorn.error``, ``uvicorn.access``). The root logger keeps no handler and
level WARNING, and "moltrust" had neither a handler nor a level, so every
``logger.info`` under it was dropped at the level check, and a warning reached
stderr only through logging's last-resort handler, bare message without level
or logger name. In the journal that left uvicorn's access lines and tracebacks
and nothing of ours.

``configure()`` puts one stderr handler on "moltrust" at INFO. Children such as
"moltrust.ipfs" reach it by propagation and need nothing of their own.

- Idempotent: a second call (reload, a test importing app.main again) finds the
  handler by its marker and adds none.
- No duplicate lines: "moltrust" keeps ``propagate = True`` so pytest's caplog
  and anything else listening on the root still see the records; in production
  the root has no handler (uvicorn leaves it alone), so each record is written
  once. tests/test_app_logging.py holds that premise against uvicorn's config.
- No other library's level changes: only the "moltrust" logger is touched.
"""
from __future__ import annotations

import logging
import sys

LOGGER_NAME = "moltrust"
FORMAT = "%(levelname)s %(name)s: %(message)s"
_MARKER = "_moltrust_stderr_handler"


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever ``sys.stderr`` is at emit time.

    Bound once at import, a handler would keep the stream it saw then; under
    pytest that is a capture buffer that is closed later. logging's own
    last-resort handler resolves it per record for the same reason.
    """

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stderr


def configure() -> logging.Handler:
    """Attach the stderr handler to "moltrust" once and return it."""
    logger = logging.getLogger(LOGGER_NAME)
    for h in logger.handlers:
        if getattr(h, _MARKER, False):
            return h
    handler = _StderrHandler()
    setattr(handler, _MARKER, True)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(FORMAT))
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)
    return handler
