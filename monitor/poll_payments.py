#!/usr/bin/env python3
"""
MolTrust — USDC Payment Poller
Polls Base L2 for incoming USDC transfers to MoltGuard wallet via web3.py.
Writes to payment_events + usdc_deposits, sends Telegram alerts.
Runs hourly via cron.

Note: Basescan V1 API is deprecated (2026-04), V2 requires paid plan for Base.
Uses eth_getLogs via 1rpc.io/base instead.
"""
import json, sys, logging, urllib.request, datetime, asyncio
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
BASE_RPC = "https://1rpc.io/base"

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
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 — scheme validated above
            json.loads(r.read())
    except Exception as e:
        log.error("Telegram send failed: %s", e)

async def record_to_db(tx_hash, from_addr, usdc_amount, block_num, timestamp):
    """Record payment in payment_events + usdc_deposits tables."""
    try:
        import asyncpg
        db_url = load_secret("DATABASE_URL")
        if not db_url:
            db_url = "postgresql://moltstack@localhost/moltstack"

        conn = await asyncpg.connect(db_url)
        try:
            credits = int(usdc_amount * CREDITS_PER_USDC)

            # Check if already recorded
            existing = await conn.fetchval(
                "SELECT tx_hash FROM payment_events WHERE tx_hash = $1",
                tx_hash
            )
            if existing:
                return False

            # DID reverse-lookup via wallet_links
            did = await conn.fetchval(
                "SELECT did FROM wallet_links WHERE LOWER(wallet_address) = LOWER($1)",
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

            # Write to usdc_deposits (credits table)
            try:
                await conn.execute(
                    """INSERT INTO usdc_deposits
                       (tx_hash, from_address, to_did, usdc_amount, credits_granted, block_number)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    tx_hash, from_addr, did or "unknown", usdc_amount, credits, block_num,
                )
            except Exception as e:
                log.warning("usdc_deposits insert skipped: %s", e)

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
    current_block = w3.eth.block_number

    # On first run, start from 1 hour ago (~1800 blocks on Base at 2s/block)
    if state["last_block"] == 0:
        state["last_block"] = current_block - 1800

    from_block = state["last_block"] + 1
    to_block = current_block

    if from_block > to_block:
        log.info("No new blocks to scan")
        return

    # Scan in chunks of 2000 blocks to avoid RPC limits
    CHUNK = 2000
    new_count = 0
    total_usdc = 0.0

    while from_block <= to_block:
        chunk_end = min(from_block + CHUNK - 1, to_block)
        log.info("Scanning blocks %d to %d...", from_block, chunk_end)

        try:
            raw_logs = get_usdc_transfers(from_block, chunk_end)
        except Exception as e:
            log.error("getLogs failed: %s", e)
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

    log.info("Done. %d new payment(s), %.2f USDC total.", new_count, total_usdc)

if __name__ == "__main__":
    main()
