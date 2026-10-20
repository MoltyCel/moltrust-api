"""Keep query strings out of the uvicorn access log.

Callers of /mcp pass their API key as ?api_key=..., and uvicorn's access log
wrote the full path, query string included, to the journal: 10,806 lines
between 2026-07-10 and 2026-10-08, the keys of 57 callers among them. The
filter below rewrites the path argument of every access record to the path
alone, plus a marker when a query was present, before any handler formats it.

It is installed on the logger, not on a handler: uvicorn builds its handlers
from its own logging config when the server starts, and that config replaces
handlers but leaves logger filters in place.
"""

import logging

ACCESS_LOGGER = "uvicorn.access"
MARKER = "?[query removed]"


class DropQueryString(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn: (client_addr, method, full_path, http_version, status_code)
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str) and "?" in args[2]:
            path = args[2].split("?", 1)[0]
            record.args = (args[0], args[1], path + MARKER, args[3], args[4])
        return True


def install() -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, DropQueryString) for f in logger.filters):
        logger.addFilter(DropQueryString())
