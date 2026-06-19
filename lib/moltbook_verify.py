"""Shared Moltbook verification-challenge solver for MolTrust agents.

Moltbook `/verify` presents a noisy ("lobster-math") word problem; the answer
MUST be a number formatted to two decimals (e.g. "15.00"). Since ~2026-06-17 the
challenges became multi-step, which the legacy single-operation regex solver gets
wrong -> HTTP 400 "Incorrect answer". Primary path is now an LLM solver
(Claude Haiku); the legacy regex solver is kept as `legacy_regex_solve()` and
used only as a fallback when the LLM path fails entirely (e.g. API outage).

Drop-in: `solve_challenge(challenge_text, verification_code=None)` returns a
"%.2f" string or None — same contract the per-agent solvers had.
"""
import os
import re
import logging
import time
from pathlib import Path

import httpx

MODEL = "claude-haiku-4-5-20251001"
SYSTEM_PROMPT = (
    "You solve a math word problem. Return ONLY the numeric answer formatted to "
    "two decimal places (e.g. 15.00). No prose, no units, no thousand separators."
)
LOG_FILE = Path("/home/moltstack/moltstack/logs/verify-solver.log")

log = logging.getLogger("moltbook_verify")
if not log.handlers:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _h = logging.FileHandler(LOG_FILE)
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(_h)
        log.setLevel(logging.INFO)
    except Exception:
        pass


def _anthropic_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    if not k:
        f = Path.home() / ".anthropic_key"
        if f.exists():
            k = f.read_text().strip()
    return k


def _format_answer(raw: str) -> str | None:
    """Extract a number from arbitrary model text and format to 2 decimals."""
    if not raw:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", raw.replace(",", ""))
    if not m:
        return None
    try:
        return f"{float(m.group()):.2f}"
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Primary: LLM solver (robust to multi-step word problems + format drift)
# ---------------------------------------------------------------------------
def llm_solve(text: str) -> str | None:
    key = _anthropic_key()
    if not key:
        log.error("no ANTHROPIC key available")
        return None
    for attempt in range(3):
        try:
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL,
                    "max_tokens": 20,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": text}],
                },
                timeout=20,
            )
            if r.status_code == 200:
                raw = (r.json().get("content") or [{}])[0].get("text", "").strip()
                ans = _format_answer(raw)
                log.info("challenge=%r llm_raw=%r answer=%s", text[:120], raw, ans)
                return ans
            log.warning("anthropic %s: %s (attempt %d/3)", r.status_code, r.text[:200], attempt + 1)
        except Exception as e:
            log.error("anthropic error: %s (attempt %d/3)", e, attempt + 1)
        time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# Legacy single-step regex solver (kept as fallback only — DO NOT delete)
# ---------------------------------------------------------------------------
def _collapse(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


_NUM_BASE = [
    ("zero", 0), ("one", 1), ("two", 2), ("three", 3), ("four", 4),
    ("five", 5), ("six", 6), ("seven", 7), ("eight", 8), ("nine", 9),
    ("ten", 10), ("eleven", 11), ("twelve", 12), ("thirteen", 13),
    ("fourteen", 14), ("fifteen", 15), ("sixteen", 16), ("seventeen", 17),
    ("eighteen", 18), ("nineteen", 19), ("twenty", 20), ("thirty", 30),
    ("forty", 40), ("fifty", 50), ("sixty", 60), ("seventy", 70),
    ("eighty", 80), ("ninety", 90),
]
NUM_LOOKUP: dict[str, int] = {}
for _w, _v in _NUM_BASE:
    NUM_LOOKUP[_w] = _v
    _c = _collapse(_w)
    if _c != _w:
        NUM_LOOKUP[_c] = _v

_OP_BASE = [
    ("plus", "+"), ("add", "+"), ("added", "+"), ("adding", "+"), ("adds", "+"),
    ("minus", "-"), ("subtract", "-"), ("subtracted", "-"),
    ("less", "-"), ("reduced", "-"), ("reduces", "-"),
    ("decreased", "-"), ("decreases", "-"), ("decrease", "-"),
    ("slows", "-"), ("slowed", "-"),
    ("times", "*"), ("multiplied", "*"), ("multiply", "*"),
    ("divided", "/"), ("divides", "/"), ("over", "/"),
]
OP_LOOKUP: dict[str, str] = {}
for _w, _o in _OP_BASE:
    OP_LOOKUP[_w] = _o
    _c = _collapse(_w)
    if _c != _w:
        OP_LOOKUP[_c] = _o


def _combine_tens_units(nums: list) -> list:
    combined = []
    i = 0
    while i < len(nums):
        v = nums[i]
        if 20 <= v <= 90 and i + 1 < len(nums) and 1 <= nums[i + 1] <= 9:
            combined.append(v + nums[i + 1])
            i += 2
        else:
            combined.append(v)
            i += 1
    return combined


def _compute(a: float, b: float, op: str) -> str | None:
    if op == "+":
        result = a + b
    elif op == "-":
        result = a - b
    elif op == "*":
        result = a * b
    elif op == "/":
        result = a / b if b != 0 else 0
    else:
        return None
    return f"{result:.2f}"


def legacy_regex_solve(text: str) -> str | None:
    literal_op = None
    for sym, op_name in [("+", "+"), ("-", "-"), ("*", "*"), ("/", "/")]:
        pat = r"(?<=\s)" + re.escape(sym) + r"(?=\s)"
        if re.search(pat, text):
            literal_op = op_name
            break
    clean = re.sub(r"[^a-zA-Z ]+", "", text).lower()
    words = [_collapse(w) for w in clean.split() if w]
    nums: list[int] = []
    op: str | None = literal_op
    for w in words:
        if w in NUM_LOOKUP:
            nums.append(NUM_LOOKUP[w])
        elif w in OP_LOOKUP and op is None:
            op = OP_LOOKUP[w]
    if op is None:
        for i in range(len(words) - 1):
            compound = words[i] + words[i + 1]
            if compound in OP_LOOKUP:
                op = OP_LOOKUP[compound]
                break
    combined = _combine_tens_units(nums)
    if len(combined) >= 2 and op is not None:
        return _compute(combined[0], combined[1], op)
    digits = [float(d) for d in re.findall(r"\d+\.?\d*", text)]
    if len(digits) >= 2:
        return _compute(digits[0], digits[1], op or "*")
    return None


# ---------------------------------------------------------------------------
# Public entrypoint (drop-in for the old per-agent solve_challenge)
# ---------------------------------------------------------------------------
def solve_challenge(challenge_text: str, verification_code: str | None = None) -> str | None:
    """LLM first; regex fallback only if the LLM path fails entirely."""
    if not challenge_text:
        return None
    ans = llm_solve(challenge_text)
    if ans is not None:
        return ans
    ans = legacy_regex_solve(challenge_text)
    log.info("FALLBACK regex challenge=%r answer=%s", challenge_text[:120], ans)
    return ans
