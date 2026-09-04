#!/usr/bin/env python3
"""
MolTrust — USDC Payment Poller
Polls Base L2 for incoming USDC transfers to MoltGuard wallet via web3.py.
Writes to payment_events + usdc_deposits, sends Telegram alerts.
Runs hourly via cron.

Note: Basescan V1 API is deprecated (2026-04), V2 requires paid plan for Base.
Uses eth_getLogs via 1rpc.io/base instead.
"""
import json, os, sys, logging, urllib.request, datetime, asyncio
from pathlib import Path
from web3 import Web3

from app import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("poll_payments")

# --- Config ---
WALLET = "0x380238347e58435f40B4da1F1A045A271D5838F5"
USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6
CREDITS_PER_USDC = 100
BASE_RPC = os.environ.get("POLL_RPC_URL", "https://1rpc.io/base")

# Blocks per eth_getLogs call. 1rpc.io/base caps this at 50; the previous value
# of 2000 was never valid against it, and every hourly run since 2026-05-14
# failed with "eth_getLogs is limited to 0 - 50 blocks range". Override when
# pointing POLL_RPC_URL at an endpoint with a wider window (mainnet.base.org
# serves 10000).
CHUNK_BLOCKS = int(os.environ.get("POLL_CHUNK_BLOCKS", "50"))

# Ceiling on work per run so a large backlog is worked off over successive
# cron runs instead of one unbounded hour-long scan.
MAX_CHUNKS_PER_RUN = int(os.environ.get("POLL_MAX_CHUNKS_PER_RUN", "200"))

# Alert when the cursor falls this far behind the chain tip. At 2 s/block this
# is roughly a day — the condition that went unnoticed for three months.
LAG_ALERT_BLOCKS = int(os.environ.get("POLL_LAG_ALERT_BLOCKS", "43200"))

TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()

w3 = Web3(Web3.HTTPProvider(BASE_RPC))

# Secrets
def load_secret(name):
    secrets = {}
    with open(Path.home() / ".moltrust_secrets") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                secrets[k.strip()] = v.strip()
    return secrets.get(name, "")

TELEGRAM_BOT_TOKEN = load_secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = load_secret("TELEGRAM_CHAT_ID")

# State file
STATE_FILE = Path.home() / "moltstack/monitor/.poll_state.json"

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_block": 0}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def get_usdc_transfers(from_block, to_block):
    """Fetch USDC Transfer events TO our wallet using eth_getLogs."""
    wallet_topic = "0x" + WALLET[2:].lower().zfill(64)
    logs = w3.eth.get_logs({
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": USDC_CONTRACT,
        "topics": [TRANSFER_TOPIC, None, wallet_topic],
    })
    return logs

def send_telegram(text):
    if not notify.telegram_allowed("poll_payments.send_telegram", logger=log):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("Telegram not configured, skipping alert")
        return
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }).encode()
    _tg_url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    if not _tg_url.startswith(("http://", "https://")):
        return
    req = urllib.request.Request(
        _tg_url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 — scheme validated above  # nosec B310 - host is the literal Telegram API, only the bot token comes from env
            json.loads(r.read())
    except Exception as e:
        log.error("Telegram send failed: %s", e)

async def record_to_db(tx_hash, from_addr, usdc_amount, block_num, timestamp):
    """Record payment in payment_events + usdc_deposits tables."""
    try:
        import asyncpg
        # Same selection the app makes (app/main.py:228): DB_HOST / DB_NAME with
        # a DATABASE_URL override. The previous version read DATABASE_URL from
        # the secrets file and otherwise hardcoded the live database, so it
        # ignored the DB_NAME the test suite sets — a test run wrote into
        # production. A money path has to be isolatable.
        db_url = os.environ.get("DATABASE_URL") or load_secret("DATABASE_URL")
        if db_url:
            conn = await asyncpg.connect(db_url)
        else:
            conn = await asyncpg.connect(
                host=os.environ.get("DB_HOST", "localhost"),
                database=os.environ.get("DB_NAME", "moltstack"),
                user=os.environ.get("DB_USER", "moltstack"),
            )
        try:
            credits = int(usdc_amount * CREDITS_PER_USDC)

            # Deduplicate against BOTH tables. payment_events alone was not
            # enough: /credits/deposit writes usdc_deposits, so a transaction a
            # user had already claimed there would be granted a second time.
            existing = await conn.fetchval(
                "SELECT 1 FROM payment_events WHERE tx_hash = $1"
                " UNION ALL SELECT 1 FROM usdc_deposits WHERE tx_hash = $1 LIMIT 1",
                tx_hash
            )
            if existing:
                return False

            # DID reverse-lookup. The previous query read wallet_links, a table
            # that exists in neither the live nor the sandbox database — it
            # raised on every payment, was swallowed by the outer handler, and
            # the payment was silently dropped. agents.wallet_address is the
            # real binding: written by POST /identity/bind after an ECDSA
            # signature check, and the same anchor /credits/deposit uses.
            did = await conn.fetchval(
                "SELECT did FROM agents WHERE LOWER(wallet_address) = LOWER($1)",
                from_addr
            )

            # Write to payment_events (spec table)
            received_at = datetime.datetime.fromtimestamp(timestamp)
            await conn.execute("""
                INSERT INTO payment_events
                    (tx_hash, from_address, to_address, amount_usdc, token, did, received_at)
                VALUES ($1, $2, $3, $4, 'USDC', $5, $6)
                ON CONFLICT (tx_hash) DO NOTHING
            """, tx_hash, from_addr, WALLET, usdc_amount, did, received_at)

            # Write to usdc_deposits only for a known sender. The previous code
            # wrote to_did='unknown', which (a) violates the foreign key to
            # agents(did) and (b) would consume the tx_hash under the UNIQUE
            # constraint, so the genuine owner claiming later via
            # /credits/deposit got 409 "already claimed" and never received the
            # credits. An unbound sender now leaves usdc_deposits untouched and
            # the transaction stays claimable.
            if did:
                try:
                    await conn.execute(
                        """INSERT INTO usdc_deposits
                           (tx_hash, from_address, to_did, usdc_amount, credits_granted, block_number)
                           VALUES ($1, $2, $3, $4, $5, $6)""",
                        tx_hash, from_addr, did, usdc_amount, credits, block_num,
                    )
                except Exception as e:
                    log.warning("usdc_deposits insert skipped: %s", e)
            else:
                log.info("  sender %s not bound to a DID — left claimable", from_addr[:12])

            # Grant credits if DID known
            if did:
                await conn.execute(
                    """INSERT INTO credit_balances (did, balance) VALUES ($1, $2)
                       ON CONFLICT (did) DO UPDATE SET balance = credit_balances.balance + $2""",
                    did, credits
                )
                log.info("  -> Credited %d credits to %s", credits, did)

                # MoltGraph: record payment as graph edge
                try:
                    await conn.execute(
                        "INSERT INTO graph_edges (from_did, to_did, context, outcome_score, source, interaction_at)"
                        " VALUES ($1, $2, 'payment', $3, 'usdc_poll', to_timestamp($4))",
                        did, 'did:moltrust:moltguard_wallet', min(1.0, usdc_amount / 5.0), timestamp
                    )
                except Exception as ge:
                    log.warning("graph_edge insert skipped: %s", ge)

            return True
        finally:
            await conn.close()
    except ImportError:
        log.warning("asyncpg not installed, skipping DB recording")
        return False
    except Exception as e:
        log.error("DB error: %s", e)
        return False

def main():
    state = load_state()

    # Even reaching the chain can fail. Unhandled, this exits with a traceback
    # into the cron log and nobody sees it.
    try:
        current_block = w3.eth.block_number
    except Exception as e:
        log.error("cannot reach %s: %s", BASE_RPC, e)
        send_telegram(
            "\U0001f6a8 <b>USDC poller cannot reach the chain</b>\n\n"
            "<b>RPC:</b> <code>%s</code>\n<b>Error:</b> <code>%s</code>"
            % (BASE_RPC, str(e)[:300])
        )
        return 1

    # On first run, start from 1 hour ago (~1800 blocks on Base at 2s/block)
    if state["last_block"] == 0:
        state["last_block"] = current_block - 1800

    from_block = state["last_block"] + 1
    to_block = current_block

    if from_block > to_block:
        log.info("No new blocks to scan")
        return 0

    lag = to_block - state["last_block"]
    if lag > LAG_ALERT_BLOCKS:
        log.warning("cursor is %d blocks behind the tip (~%.1f days)", lag, lag * 2 / 86400)
        send_telegram(
            "\u26a0\ufe0f <b>USDC poller is behind</b>\n\n"
            "Cursor is <b>%d blocks</b> behind the chain tip (~%.1f days).\n"
            "Working it off at %d blocks/run."
            % (lag, lag * 2 / 86400, CHUNK_BLOCKS * MAX_CHUNKS_PER_RUN)
        )

    new_count = 0
    total_usdc = 0.0
    chunks_done = 0
    failed = None

    while from_block <= to_block and chunks_done < MAX_CHUNKS_PER_RUN:
        chunk_end = min(from_block + CHUNK_BLOCKS - 1, to_block)
        log.info("Scanning blocks %d to %d...", from_block, chunk_end)

        try:
            raw_logs = get_usdc_transfers(from_block, chunk_end)
        except Exception as e:
            # Do NOT advance the cursor past a range we failed to read — that
            # would mark unscanned blocks as processed and lose any payment in
            # them. The cursor stays on the last chunk that actually succeeded.
            # And the run must be loud: a silent failure reporting "0 new
            # payments" is what kept this broken from 2026-05-14 to 2026-09-03.
            failed = f"blocks {from_block}-{chunk_end}: {e}"
            log.error("getLogs failed for %s", failed)
            break

        for entry in raw_logs:
            from_addr = "0x" + entry["topics"][1].hex()[-40:]
            raw_amount = int(entry["data"].hex(), 16)
            usdc_amount = raw_amount / (10 ** USDC_DECIMALS)
            tx_hash = entry["transactionHash"].hex()
            block_num = entry["blockNumber"]

            # Get block timestamp
            try:
                block_data = w3.eth.get_block(block_num)
                timestamp = block_data["timestamp"]
            except Exception:
                timestamp = int(datetime.datetime.utcnow().timestamp())

            log.info("  TX: %s... | %.2f USDC from %s... | Block %d",
                     tx_hash[:16], usdc_amount, from_addr[:10], block_num)

            recorded = asyncio.run(record_to_db(
                tx_hash, from_addr, usdc_amount, block_num, timestamp
            ))

            if recorded:
                new_count += 1
                total_usdc += usdc_amount

                time_str = datetime.datetime.fromtimestamp(
                    timestamp, tz=datetime.timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")

                send_telegram(
                    "\U0001f4b0 <b>USDC Payment Received</b>\n\n"
                    "<b>Amount:</b> %.2f USDC (%d credits)\n"
                    "<b>From:</b> <code>%s</code>\n"
                    "<b>TX:</b> <a href=\"https://basescan.org/tx/0x%s\">%s...</a>\n"
                    "<b>Time:</b> %s"
                    % (usdc_amount, int(usdc_amount * CREDITS_PER_USDC),
                       from_addr, tx_hash, tx_hash[:16], time_str)
                )

        state["last_block"] = chunk_end
        save_state(state)
        from_block = chunk_end + 1
        chunks_done += 1

    remaining = to_block - state["last_block"]

    if failed:
        # A failed poll is an incident, not a quiet zero. Alert, then exit
        # non-zero so cron surfaces it too.
        log.error("ABORTED after %d chunk(s): %s", chunks_done, failed)
        send_telegram(
            "\U0001f6a8 <b>USDC poller failed</b>\n\n"
            "<b>Error:</b> <code>%s</code>\n"
            "<b>Scanned this run:</b> %d chunk(s)\n"
            "<b>Cursor:</b> %d (unchanged past the failure)\n"
            "<b>Behind tip:</b> %d blocks\n\n"
            "No blocks were marked processed beyond the last successful chunk."
            % (failed[:300], chunks_done, state["last_block"], remaining)
        )
        log.info("Done with errors. %d new payment(s), %.2f USDC total, %d blocks behind.",
                 new_count, total_usdc, remaining)
        return 1

    if remaining > 0:
        log.info("Done. %d new payment(s), %.2f USDC total. %d blocks still to scan "
                 "(run cap %d chunks) — next run continues.",
                 new_count, total_usdc, remaining, MAX_CHUNKS_PER_RUN)
    else:
        log.info("Done. %d new payment(s), %.2f USDC total. Caught up to block %d.",
                 new_count, total_usdc, state["last_block"])
    return 0

if __name__ == "__main__":
    sys.exit(main())
