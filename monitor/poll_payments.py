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

# Der Pfad zum Repo, aus der Datei selbst. Python legt beim Skriptaufruf
# das Verzeichnis des Skripts auf sys.path, nicht das
# Arbeitsverzeichnis — ohne diese Zeile braucht der Aufruf ein
# PYTHONPATH aus der Crontab, und eine Crontab, die den Suchpfad setzt,
# ist dieselbe unsichtbare Ueberstimmung wie POLL_RPC_URL am 09.10.2026.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("poll_payments")

# --- Config ---
WALLET = "0x380238347e58435f40B4da1F1A045A271D5838F5"
USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6
CREDITS_PER_USDC = 100
# Der Name sagt, was gelesen wird. Bis zum 09.10.2026 hiess diese Variable
# BASE_RPC und las POLL_RPC_URL — deshalb fand die URL-Durchsicht vom 07.10.
# diese Stelle nicht, und deshalb lief der Poller sechzehn Stunden gegen
# mainnet.base.org, waehrend BASE_RPC in den Secrets auf den eigenen Anbieter
# zeigte. Die Crontab setzte POLL_RPC_URL fest und ueberstimmte alles.

# Blocks per eth_getLogs call. Fifty was the ceiling of 1rpc.io/base, and the
# default stayed there after the endpoint moved. Fifty times the two hundred
# chunks below is ten thousand blocks an hour, while Base produces eighteen
# hundred — a backlog of 28,900 blocks, as on 2026-10-09, would have taken
# three hours to clear, and only if nothing failed. Two thousand is what the
# configured endpoint answered during the manual catch-up that day: 28,932
# blocks in fifteen chunks, none refused.
CHUNK_BLOCKS = int(os.environ.get("POLL_CHUNK_BLOCKS", "2000"))

# Ceiling on work per run so a large backlog is worked off over successive
# cron runs instead of one unbounded hour-long scan.
MAX_CHUNKS_PER_RUN = int(os.environ.get("POLL_MAX_CHUNKS_PER_RUN", "200"))

# Alert when the cursor falls this far behind the chain tip. Six hundred blocks
# is about twenty minutes at two seconds a block.
#
# It was 43,200 — roughly a day. On 2026-10-09 the poller stood still for
# sixteen hours and the backlog alarm never fired; what reported was the failed
# call, forty minutes after the standstill began and only because the call
# failed. A poller that stops succeeding quietly looks exactly like one with
# nothing to do, and the lag is the signal that tells them apart.
# Aus dem Takt gerechnet, nicht geraten.
#
# Base erzeugt einen Block je zwei Sekunden: 1800 je Stunde. Der Poller laeuft
# stuendlich (Crontab-Zeile 58, `0 * * * *`), also ist der Cursor zu Beginn
# jedes Laufs rund 1800 Bloecke hinter der Spitze. Das ist der Takt und kein
# Rueckstand.
#
# Die Schwelle muss darueber liegen, sonst meldet die Wache jeden Lauf. Genau
# das ist am 09.10.2026 passiert: 43200 (etwa ein Tag) hat den
# sechzehnstuendigen Stillstand nie erreicht, und die Korrektur auf 600 lag
# unter dem Takt und meldete elf Mal an einem Tag, waehrend jede Laufzeile mit
# `rueckstand=0 ergebnis=ok` endete.
#
# Drei Takte: ein Lauf, der einmal aussetzt, bleibt still; zwei ausgesetzte
# Laeufe melden. Ein echter Stillstand ist damit nach drei Stunden sichtbar,
# nicht nach einem Tag und nicht jede Stunde.
BLOECKE_JE_SEKUNDE = 0.5          # Base: ein Block je zwei Sekunden
LAUFABSTAND_SEKUNDEN = 3600       # Crontab-Zeile 58: stuendlich
BLOECKE_JE_LAUF = int(LAUFABSTAND_SEKUNDEN * BLOECKE_JE_SEKUNDE)   # 1800
LAG_ALERT_BLOCKS = int(os.environ.get(
    "POLL_LAG_ALERT_BLOCKS", str(3 * BLOECKE_JE_LAUF)))            # 5400

from app.base_rpc import base_rpc_url  # noqa: E402
from monitor.hexutil import hex0x as _hex0x, TRANSFER_TOPIC  # noqa: E402

def rpc_host() -> str:
    """Nur der Host des Endpunkts.

    Der konfigurierte Endpunkt traegt den Schluessel im Pfad. Ein Protokoll,
    das die ganze URL schreibt, legt ihn im Klartext ab — derselbe Fehler, der
    am 20.09.2026 den Telegram-Token 220-mal in watchdog.log geschrieben hat.
    Beim ersten Probelauf am 09.10. stand er genau so in der Laufzeile.
    """
    from urllib.parse import urlparse
    return urlparse(base_rpc_url()).netloc or "?"


_w3_cache = None


def w3_client():
    """Der Web3-Client, beim ersten Gebrauch gebaut.

    Nicht beim Import: `base_rpc_url()` bricht ab, wenn BASE_RPC fehlt, und
    ein Modul zu importieren ist keine Benutzung des Endpunkts. Sonst faellt
    jeder Testlauf ohne gesetzte Variable schon beim Import um.
    """
    global _w3_cache
    if _w3_cache is None:
        _w3_cache = Web3(Web3.HTTPProvider(base_rpc_url()))
    return _w3_cache

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
    logs = w3_client().eth.get_logs({
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": USDC_CONTRACT,
        "topics": [TRANSFER_TOPIC, None, wallet_topic],
    })
    return logs

def send_telegram(text, *, channel: str = notify.MONEY):
    """Ueber notify, nicht daran vorbei.

    Bis zum 09.10.2026 baute diese Funktion ihre eigene Nutzlast und schickte
    sie selbst. Sie benutzte notify nur fuer das Gate und die Chat-ID — und
    umging damit die Drosselung und das Sendeprotokoll. Derselbe Bypass wie in
    deploy.sh: wer selbst sendet, erscheint in keiner Zeile von
    ~/selftest/telegram-sent.jsonl und wird bei Wiederholung nicht gezaehlt.
    """
    return notify.send_telegram(text, channel=channel, parse_mode="HTML")

def main():
    state = load_state()

    # Even reaching the chain can fail. Unhandled, this exits with a traceback
    # into the cron log and nobody sees it.
    try:
        current_block = w3_client().eth.block_number
    except Exception as e:
        log.error("cannot reach %s: %s", rpc_host(), e)
        send_telegram(
            "\U0001f6a8 <b>USDC poller cannot reach the chain</b>\n\n"
            "<b>RPC:</b> <code>%s</code>\n<b>Error:</b> <code>%s</code>"
            % (rpc_host(), str(e)[:300])
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
        # Unabhaengig davon, ob ein Aufruf gescheitert ist. Am 09.10.2026 stand
        # der Poller sechzehn Stunden; gemeldet hat der Fehlschlag, vierzig
        # Minuten nach dem Stillstand. Ein Poller, der leise aufhoert zu
        # liefern, sieht aus wie einer ohne Arbeit — der Rueckstand trennt die
        # beiden.
        stunden = lag * 2 / 3600
        log.warning("cursor %d is %d blocks behind tip %d (~%.1f h)",
                    state["last_block"], lag, current_block, stunden)
        send_telegram(
            "\u26a0\ufe0f <b>USDC-Poller haengt zurueck</b>\n\n"
            "<b>Cursor:</b> %d\n<b>Kettenspitze:</b> %d\n"
            "<b>Abstand:</b> %d Bloecke, rund %.1f Stunden\n"
            "<b>Schwelle:</b> %d Bloecke (%d je Lauf x 3)\n"
            "<b>Endpunkt:</b> <code>%s</code>\n\n"
            "Aufarbeitung mit %d Bloecken je Lauf."
            % (state["last_block"], current_block, lag, stunden,
               LAG_ALERT_BLOCKS, BLOECKE_JE_LAUF, rpc_host(),
               CHUNK_BLOCKS * MAX_CHUNKS_PER_RUN)
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
            from_addr = "0x" + _hex0x(entry["topics"][1])[-40:]
            raw_amount = int(_hex0x(entry["data"]), 16)
            usdc_amount = raw_amount / (10 ** USDC_DECIMALS)
            tx_hash = _hex0x(entry["transactionHash"])
            block_num = entry["blockNumber"]

            # Get block timestamp
            try:
                block_data = w3_client().eth.get_block(block_num)
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

    # Eine Zeile je Lauf, auch bei Erfolg. Vorher nannte das Protokoll den
    # Host nur im Fehlerfall, und "x von y Aufrufen" blieb eine Frage ohne
    # Quelle — am 09.10. liessen sich 16 Fehlschlaege zaehlen und kein
    # einziger gelungener Aufruf.
    log.info("LAUF endpunkt=%s aufrufe=%d bloecke=%d cursor=%d spitze=%d "
             "rueckstand=%d ergebnis=%s",
             rpc_host(), chunks_done, chunks_done * CHUNK_BLOCKS,
             state["last_block"], current_block, remaining,
             "ok" if not failed else "abgebrochen")

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
