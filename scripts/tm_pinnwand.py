#!/usr/bin/env python3
"""TM-PIN — taegliche Pinnwand auf taskmarket.

Legt einmal am Tag einen bezahlten Task an, der die Dauerrunde und den
Empfehlungslink mit der Ambassador-DID nennt. Die Einsendungen werden am Markt
nicht abgerechnet; gezahlt wird ueber die Dauerrunde (G4-5).

Der Marktaufruf laeuft nur mit --scharf; ohne das Flag rechnet und rendert das
Skript nur. Vor jeder Anlage laeuft scripts/runde_vorpruefung.py gegen den
Tasktext und bricht bei rot ab: ein Aufgabentext ist nach der Anlage
unveraenderlich, `task update` kennt die Beschreibung nicht.

Antwortet der Markt nicht, gibt es keinen Task, der Grund steht im Log und der
Lauf endet mit rc 0 (Kriterium 7). Jeder angelegte Task bekommt eine Zeile in
ops/runden/budget.jsonl und steht in data/tm_pinnwand_state.json, bis er nach
24 h entweder per refund-expired zurueckfliesst (null Einsendungen) oder als
verbraucht gebucht wird (mit Einsendungen; refund-expired greift dann nicht).

Zum Buch: ops/runden/budget.jsonl liegt im privaten Ops-Repo, der Server hat
dort kein Schreibrecht und kein Skript schreibt die Datei (geprueft 30.10.2026).
Dieser Lauf fuehrt deshalb ein eigenes Buch in derselben Zeilenform unter
data/tm_pinnwand_budget.jsonl; der Koordinator traegt die Zeilen von dort in
ops/runden/budget.jsonl nach (Kriterium 3). Beim Einrichten wird das Buch mit
der letzten Zeile aus dem Repo angelegt, damit `rest` fortgeschrieben und nicht
neu erfunden wird.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

# Konstanten aus scripts/dauerrunde.py, am Merge-Stand 4f8347d gelesen (G4-3).
WOCHENBUDGET_USDC = Decimal("5")
DECKEL_JE_AKTIVIERUNG_USDC = Decimal("0.05")
FENSTER_TAGE = 14
BEZAHLT_JE_BETREIBER = 3

# G5-Entscheidung Lars 23.10.2026, Masterauftrag :3400-3402.
TAGESDECKEL_USDC = Decimal("0.48")
BUDGETENDE = date(2026, 11, 30)
# Ruecklage fuer G4 bis zum Budgetende. Der Wochendeckel von 5 USDC waere ueber
# die Restwochen groesser als das freie Budget und wuerde den Tagesbetrag auf
# null druecken; deshalb die gemessene Rate plus diese Untergrenze.
G4_RUECKLAGE_MIN_USDC = Decimal("1")

AMBASSADOR_DID = "did:moltrust:214298170282752d"
JOIN_URL = f"https://api.moltrust.ch/join?ref={AMBASSADOR_DID}"
RUNDEN_SEITE = "https://moltrust.ch/rounds/dauer/"
TITEL = "MolTrust Dauerrunde — register, activate, get paid"
LAUFZEIT_H = 24
TAGS = "identity,agent,onboarding,moltrust"
MODE = "bounty"

# Die CLI liegt in keinem Login-Pfad; die Cron-Zeilen setzen den PATH selbst.
CLI = str(Path("~/.npm-global/bin/taskmarket").expanduser())
# Jeder Schreibaufruf der CLI kostet 0,001 USDC Relaisgebuehr (CLI-Hilfe).
RELAISGEBUEHR_USDC = Decimal("0.001")
VORPRUEFUNG = Path(__file__).resolve().parent / "runde_vorpruefung.py"


def freies_budget(budget_jsonl: Path) -> Decimal | None:
    """Rest aus der letzten Zeile des Buchs, oder None wenn es keine gibt.

    Ohne Budgetstand gibt es keinen Tagesbetrag und keinen Task. Das ist kein
    Abbruch: der Grund steht im Log und der Lauf endet mit rc 0.
    """
    rest = None
    if not budget_jsonl.exists():
        return None
    for zeile in budget_jsonl.read_text().splitlines():
        if not zeile.strip():
            continue
        rest = Decimal(json.loads(zeile)["rest"])
    return rest


def g4_erwartung(aktivierungen_7t: int, resttage: int) -> Decimal:
    """Erwartete G4-Auszahlungen bis zum Budgetende aus der gemessenen Rate."""
    rate_je_tag = Decimal(aktivierungen_7t) / Decimal(7)
    erwartet = (rate_je_tag * Decimal(resttage) * DECKEL_JE_AKTIVIERUNG_USDC)
    return max(erwartet, G4_RUECKLAGE_MIN_USDC)


def tagesbetrag(frei: Decimal, erwartet: Decimal, resttage: int) -> Decimal:
    if resttage <= 0:
        return Decimal("0")
    roh = (frei - erwartet) / Decimal(resttage)
    if roh <= 0:
        return Decimal("0")
    return min(roh, TAGESDECKEL_USDC).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)


def resttage(heute: date) -> int:
    return max((BUDGETENDE - heute).days, 0)


def beschreibung(betrag: Decimal) -> str:
    """Erste Zeile ist der Titel — die CLI kennt kein --title."""
    return "\n".join([
        f"Title: {TITEL}",
        "",
        "MolTrust runs a standing round for agent identity. Register, make one "
        "authenticated call, and the round pays you.",
        "",
        f"Join through this link so the round can see where you came from: {JOIN_URL}",
        "",
        "The task: register over the link above, then make your first "
        "authenticated call against the MolTrust API.",
        "",
        "How the round pays, and the current numbers:",
        f"- weekly budget {WOCHENBUDGET_USDC} USDC",
        f"- up to {DECKEL_JE_AKTIVIERUNG_USDC} USDC per activation",
        f"- a window of {FENSTER_TAGE} days, settled every Monday 06:00 UTC",
        f"- up to {BEZAHLT_JE_BETREIBER} paid activations per operator",
        f"- rules and the running numbers: {RUNDEN_SEITE}",
        "",
        "When an activation does not count:",
        "- a self-referral, or a mutual referral between two registrations",
        "- no first authenticated call, or a call not prescribed by any task text",
        "- the activation lands later than 14 days after the registration",
        "- the operator is on the exclusion list "
        "(ausgeschlossene-betreiber.json)",
        "- the registration carries no operator_did",
        "- referrer and referred are not independent: they share a wallet "
        "or a key instead of using a different wallet each",
        "- the operator already has three paid activations in the week of "
        "this window",
        "",
        "The escrow on this task stays with the market and returns to us when "
        "the task runs out; submissions on this task are not settled here. Your "
        "registration is paid through the round, every Monday 06:00 UTC. "
        "Register over the link with our own DID as referrer and the full "
        "activation amount goes to you.",
    ])


def cli(*argv: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """Kein Aufruf der CLI darf den Lauf abreissen (Kriterium 7).

    Fehlt die CLI im PATH oder antwortet sie nicht, kommt dasselbe zurueck wie
    bei einem Fehlschlag: rc ungleich null und der Grund im Text.
    """
    try:
        return subprocess.run([CLI, *argv], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(
            argv, 127, "", f"CLI nicht erreichbar: {e.__class__.__name__}")


def markt_antwortet() -> tuple[bool, str]:
    """Schweigeprobe: antwortet der Markt nicht, gibt es keinen Task."""
    try:
        p = subprocess.run(
            [CLI, "task", "list", "--limit", "1"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"CLI nicht erreichbar: {e.__class__.__name__}"
    if p.returncode != 0:
        return False, f"CLI rc {p.returncode}"
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        return False, "Antwort ist kein JSON"
    if not d.get("ok"):
        return False, f"Markt meldet nicht ok: {str(d.get('error'))[:200]}"
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-jsonl", type=Path, required=True,
                    help="Buch in der Zeilenform von ops/runden/budget.jsonl")
    ap.add_argument("--aktivierungen-7t", type=int, default=0,
                    help="Aktivierungen der letzten sieben Tage, Quelle registry")
    ap.add_argument("--vorpruefung", type=Path, default=VORPRUEFUNG,
                    help="Pfad von runde_vorpruefung.py (Pflicht-Tor)")
    ap.add_argument("--state", type=Path,
                    default=Path("data/tm_pinnwand_state.json"),
                    help="offene Tasks zwischen den Laeufen")
    ap.add_argument("--scharf", action="store_true",
                    help="Task wirklich anlegen; ohne das Flag nur rechnen")
    args = ap.parse_args()

    heute = datetime.now(timezone.utc).date()
    tage = resttage(heute)
    frei = freies_budget(args.budget_jsonl)
    if frei is None:
        print(f"kein Task: kein Budgetstand in {args.budget_jsonl} "
              "(Buch mit der letzten Zeile aus ops/runden/budget.jsonl anlegen)")
        return 0
    frei = abschliessen(args, frei)
    erwartet = g4_erwartung(args.aktivierungen_7t, tage)
    betrag = tagesbetrag(frei, erwartet, tage)

    print(f"{heute.isoformat()} resttage={tage} frei={frei} "
          f"g4_erwartet={erwartet} tagesbetrag={betrag} deckel={TAGESDECKEL_USDC}")

    if betrag <= 0:
        print("kein Task: Tagesbetrag null (Budget reicht nicht bis 30.11.)")
        return 0

    erreichbar, grund = markt_antwortet()
    if not erreichbar:
        print(f"kein Task: {grund}")
        return 0

    text = beschreibung(betrag)
    rot = vorpruefung_rot(text, args.vorpruefung)
    if rot is not None:
        print(f"kein Task: Vorpruefung rot — {rot}")
        return 0
    print("Vorpruefung gruen")

    if not args.scharf:
        print("--- Tasktext ---")
        print(text)
        return 0

    task_id, tx, rohdaten = anlegen(text, betrag)
    if task_id is None:
        print(f"kein Task: Anlage ohne Task-ID — {rohdaten[:300]}")
        return 0
    print(f"angelegt task_id={task_id} escrow={betrag} tx={tx}")

    rest = frei - betrag
    budgetzeile(args.budget_jsonl, {
        "zweck": f"TM-PIN Pinnwand-Task {task_id}, Escrow {betrag} "
                 f"(Tagesbetrag G5, Laufzeit {LAUFZEIT_H} h)",
        "betrag": str(betrag),
        "empfänger": "taskmarket escrow",
        "tx": tx or "",
        "rest": str(rest),
    })
    offene_merken(args.state, task_id, betrag, tx)
    return 0


def vorpruefung_rot(text: str, pruefer: Path) -> str | None:
    """Pflicht-Tor vor der Anlage. Gibt den Grund zurueck, wenn rot.

    Laeuft auch im Trockenlauf: der Text ist nach der Anlage unveraenderlich,
    also soll jeder Lauf zeigen, ob die Fassung von heute durchgeht.
    """
    if not pruefer.exists():
        return f"{pruefer} fehlt"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write(text)
        pfad = fh.name
    try:
        p = subprocess.run([sys.executable, str(pruefer), pfad,
                            "--runde", "dauer"],
                           capture_output=True, text=True, timeout=180)
    finally:
        Path(pfad).unlink(missing_ok=True)
    if p.returncode == 0:
        return None
    zeilen = [z.strip() for z in p.stdout.splitlines() if "rot" in z]
    return "; ".join(zeilen) or f"rc {p.returncode}"


def _erstes(d, *namen):
    for name in namen:
        wert = d.get(name)
        if wert:
            return str(wert)
    return None


def anlegen(text: str, betrag: Decimal) -> tuple[str | None, str | None, str]:
    """Legt den Task an. Die Antwort tragen wir unveraendert ins Log."""
    p = cli("task", "create", "--description", text, "--reward", str(betrag),
            "--duration", str(LAUFZEIT_H), "--mode", MODE, "--tags", TAGS,
            timeout=300)
    roh = (p.stdout or "") + (p.stderr or "")
    print(f"--- Antwort task create (rc {p.returncode}) ---\n{roh.strip()}")
    if p.returncode != 0:
        return None, None, roh
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None, None, roh
    if not d.get("ok"):
        return None, None, roh
    daten = d.get("data") or {}
    return (_erstes(daten, "taskId", "task_id", "id"),
            _erstes(daten, "txHash", "transactionHash", "tx"),
            roh)


def budgetzeile(budget_jsonl: Path, felder: dict) -> None:
    zeile = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"}
    zeile.update(felder)
    with budget_jsonl.open("a") as fh:
        fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def _state(pfad: Path) -> dict:
    if not pfad.exists():
        return {"offen": []}
    return json.loads(pfad.read_text())


def offene_merken(pfad: Path, task_id: str, betrag: Decimal, tx: str | None) -> None:
    st = _state(pfad)
    st["offen"].append({
        "task_id": task_id,
        "escrow": str(betrag),
        "tx": tx or "",
        "angelegt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    st["letzter_lauf"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pfad.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n")


def einsendungen(task_id: str) -> int | None:
    p = cli("task", "get", task_id)
    if p.returncode != 0:
        return None
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    zahl = (d.get("data") or {}).get("submissionCount")
    return None if zahl is None else int(zahl)


def abschliessen(args, frei: Decimal) -> Decimal:
    """Kriterium 4: nach Ablauf zurueckholen oder als verbraucht buchen.

    refund-expired verlangt null Einsendungen (CLI-Hilfe, task_watch.py:778).
    Mit Einsendungen bleibt der Escrow am Markt gebunden; dann ist der Betrag
    verbraucht und wird so gebucht, nicht zurueckgerechnet.
    """
    st = _state(args.state)
    bleibt, rest = [], frei
    jetzt = datetime.now(timezone.utc)
    for eintrag in st.get("offen", []):
        angelegt = datetime.fromisoformat(eintrag["angelegt"])
        if (jetzt - angelegt).total_seconds() < LAUFZEIT_H * 3600:
            bleibt.append(eintrag)
            continue
        task_id = eintrag["task_id"]
        escrow = Decimal(eintrag["escrow"])
        zahl = einsendungen(task_id)
        if zahl is None:
            print(f"{task_id}: Markt antwortet nicht auf task get — bleibt offen")
            bleibt.append(eintrag)
            continue
        if zahl > 0:
            print(f"{task_id}: {zahl} Einsendungen — Escrow gebunden, "
                  f"{escrow} als verbraucht gebucht")
            if args.scharf:
                budgetzeile(args.budget_jsonl, {
                    "zweck": f"TM-PIN {task_id} verbraucht, {zahl} Einsendungen "
                             "(refund-expired greift nicht)",
                    "betrag": "0",
                    "empfänger": "taskmarket escrow",
                    "tx": "",
                    "rest": str(rest),
                })
            continue
        if not args.scharf:
            print(f"{task_id}: 0 Einsendungen, abgelaufen — refund-expired "
                  "waere jetzt faellig")
            bleibt.append(eintrag)
            continue
        p = cli("task", "refund-expired", task_id, timeout=300)
        roh = ((p.stdout or "") + (p.stderr or "")).strip()
        print(f"--- Antwort refund-expired {task_id} (rc {p.returncode}) ---\n{roh}")
        if p.returncode != 0:
            bleibt.append(eintrag)
            continue
        tx = None
        try:
            tx = _erstes((json.loads(p.stdout).get("data") or {}),
                         "txHash", "transactionHash", "tx")
        except json.JSONDecodeError:
            pass
        rest = rest + escrow - RELAISGEBUEHR_USDC
        budgetzeile(args.budget_jsonl, {
            "zweck": f"TM-PIN {task_id} Rueckfluss ohne Einsendung, "
                     f"abzueglich {RELAISGEBUEHR_USDC} Relaisgebuehr",
            "betrag": str(-(escrow - RELAISGEBUEHR_USDC)),
            "empfänger": "0xa175d51bfe0170738720DAAEc627A84d44dc9Eb9",
            "tx": tx or "",
            "rest": str(rest),
        })
    st["offen"] = bleibt
    if args.scharf:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    return rest


if __name__ == "__main__":
    sys.exit(main())
