"""Where MolTrust receives payments, in one place: BASE_ADDR.

Until 2026-10-12 the address stood as a literal in app/usdc.py,
monitor/poll_payments.py and agents/poll_payments.py, and in four more places
across moltguard and moltrust-web. The wallet rotation of 2026-10-11 had to
find and change every copy by commit (#750); a copy that is missed keeps
pointing at the old wallet and nothing says so.

BASE_ADDR in ~/.moltrust_secrets is the one source. The API reads it through
its EnvironmentFile, the poller through the crontab's `source` of the same file.
Changing the variable and restarting the reader is the whole move; no commit.

No fallback. A missing or malformed BASE_ADDR is an error at the moment of use,
not a silent default — a default address is exactly the hard-wired copy this
module replaces.
"""

from __future__ import annotations

import os
import re

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class PaymentAddressNotConfigured(RuntimeError):
    """BASE_ADDR is missing or not a 20-byte hex address."""


def payment_address() -> str:
    """The receiving address of this process, read at every call.

    Read at call time, not at import: importing a module is not a payment, and
    an import-time failure would break every test run without the variable.
    """
    value = os.getenv("BASE_ADDR", "").strip()
    if not value:
        raise PaymentAddressNotConfigured(
            "BASE_ADDR is not set. No fallback: a default address is a hard-wired copy.")
    if not _ADDRESS_RE.match(value):
        raise PaymentAddressNotConfigured("BASE_ADDR is not a 0x-prefixed 20-byte hex address.")
    return value
