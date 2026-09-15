"""Hold list: local, deterministic match of lead text against restricted topics.

The list is private operator data and is NOT part of this repo. It is loaded at
runtime from config.hold_list_path() and is never sent to the Anthropic API or
written to the queue / Telegram — a match is recorded by entry ID only.

File format (UTF-8, one entry per line):

    # comment lines and blank lines are ignored
    H01 | some phrase           plain phrase entry
    H02 | re:some\\s+regex        raw regular expression entry
    another phrase              entry without an ID -> ID "L<line number>"

Plain phrases match case-insensitively, anchored at a word start, with any run of
whitespace / hyphen / underscore / slash allowed between words, and without a
trailing word boundary (so a phrase also matches plural or inflected forms).
"re:" entries are compiled as-is with IGNORECASE. Invalid regex entries are
skipped and logged by ID.
"""
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("content_scout.hold")

# Recorded instead of an entry ID when the list could not be loaded (fail-safe).
LIST_MISSING = "hold-list-missing"
# Recorded when the lead text could not be fetched, so the match is incomplete.
TEXT_UNAVAILABLE = "text-unavailable"

_ID_LINE = re.compile(r"^\s*([A-Za-z0-9_.-]{1,32})\s*\|\s*(.+?)\s*$")
_SEP = r"[\s\-_/]*"


def _phrase_regex(phrase: str) -> str:
    words = [w for w in re.split(r"[\s\-_/]+", phrase.strip()) if w]
    return r"(?<!\w)" + _SEP.join(re.escape(w) for w in words)


class HoldList:
    def __init__(self, entries: list):
        # entries: [(entry_id, compiled_pattern)]
        self.entries = entries

    def __len__(self):
        return len(self.entries)

    def match(self, text: str) -> Optional[str]:
        """Return the ID of the first matching entry, or None. Never the matched text."""
        if not text:
            return None
        for entry_id, pat in self.entries:
            if pat.search(text):
                return entry_id
        return None


def parse(text: str) -> HoldList:
    entries = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ID_LINE.match(line)
        entry_id, body = (m.group(1), m.group(2)) if m else (f"L{n}", line)
        try:
            if body.startswith("re:"):
                pat = re.compile(body[3:].strip(), re.IGNORECASE)
            else:
                pat = re.compile(_phrase_regex(body), re.IGNORECASE)
        except re.error:
            log.error("hold list entry %s is not a valid pattern — skipped", entry_id)
            continue
        entries.append((entry_id, pat))
    return HoldList(entries)


def load(path: Path) -> Optional[HoldList]:
    """Load the hold list. Returns None (=> fail safe: hold everything) if the file
    is missing, unreadable, or has no usable entries."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        log.error("HOLD LIST MISSING or unreadable at %s (%s) — every PASS item will be "
                  "held this run", path, type(e).__name__)
        return None
    hl = parse(text)
    if not len(hl):
        log.error("HOLD LIST at %s has no usable entries — every PASS item will be held "
                  "this run", path)
        return None
    log.info("hold list loaded: %d entries", len(hl))
    return hl
