#!/usr/bin/env python3
"""Kann notify fuer alle vier Kanaele melden. Ohne zu senden.

Zwei Entscheidungen tragen diese Pruefung, und beide sind aus einem Fehlschlag
gelernt.

Erstens: mit geleerter Umgebung, in einem eigenen Prozess. Am 09.10.2026 loeste
notify die chat_id aus ~/.moltrust_secrets auf und den Token nur aus
os.environ. Auf diesem Server, mit gesourcten Secrets, war beides da — der
Fehler zeigte sich nur dort, wo keine Umgebung ist: in deploy.sh als forced
command ueber SSH. Eine Pruefung, die die Umgebung des Pruefers benutzt, waere
an diesem Tag gruen gewesen, waehrend ein gescheiterter Deploy niemanden
erreicht haette.

Zweitens: ueber den echten Sendeweg, mit abgeklemmtem Transport. Die erste
Fassung dieser Pruefung rief notify._resolve() direkt ab und blieb deshalb
gruen, als ich zur Gegenprobe die kaputte Fassung wiederherstellte — sie pruefte
eine Funktion, die nie defekt war. Geprueft werden muss der Weg, den der Sender
nimmt: Gate, Token, chat_id, Drosselung, bis unmittelbar vor requests.post.
Dort steht ein Platzhalter, der die Anfrage nicht stellt.

Es geht also nichts raus. Vier Nachrichten am Tag, um festzustellen, dass
Nachrichten ankommen, ist die Art Wache, die nach einer Woche abgeschaltet
wird.

Der Bericht nennt nie einen Wert, auch kein Praefix — nur Kanal, Grund, Laenge.

Ausgabe: letzte Zeile ist die Zahl der Beanstandungen.
"""
import json
import os
import subprocess
import sys
import tempfile

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Das Kindprogramm. Laeuft mit geleerter Umgebung und gibt nur Befunde zurueck,
# keine Werte — damit nichts Geheimes ueber eine Prozessgrenze geht, auch nicht
# in ein Zwischenergebnis.
KIND = r'''
import json, os, re, sys
sys.path.insert(0, %(wurzel)r)
from app import notify

# Der Transport wird abgeklemmt. Die URL traegt den Token, deshalb wird sie
# nicht festgehalten und nicht gezaehlt — nur, DASS es bis hierher kam.
erreicht = {}


class _Antwort:
    status_code = 200
    content = b""

    @staticmethod
    def json():
        return {"result": {"message_id": 0}}


def _platzhalter(url, **kw):
    erreicht[kw.get("data", {}).get("text", "?")] = True
    return _Antwort()


notify.requests.post = _platzhalter

aus = {"kanaele": {}, "token_laenge": len(notify._resolve("TELEGRAM_BOT_TOKEN"))}
for k in notify.CHANNELS:
    # Je Kanal ein eigener Text, sonst drosselt der erste die drei anderen weg.
    text = "pruefung-%%s-ohne-versand" %% k
    ok = notify.send_telegram(text, channel=k)
    eintrag = {"bis_zum_transport": bool(ok and erreicht.get(text))}
    if not eintrag["bis_zum_transport"]:
        eintrag["grund"] = notify.letzter_grund() or "unbekannt"
    try:
        eintrag["chat_laenge"] = len(notify.chat_id_for(k))
        eintrag["eigene_id"] = bool(
            notify._resolve("TELEGRAM_CHAT_ID_" + k.upper()))
    except Exception as e:
        eintrag["chat_fehler"] = type(e).__name__
    aus["kanaele"][k] = eintrag

sys.stdout.write(json.dumps(aus) + "\n")
sys.stdout.flush()
''' % {"wurzel": WURZEL}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="notify-pruefung-") as tmp:
        p = subprocess.run(
            [sys.executable, "-c", KIND],
            # HOME, weil ~/.moltrust_secrets sonst nicht gefunden wird — das ist
            # der Weg, den die Pruefung gehen soll. MOLTRUST_NOTIFY, weil das
            # Gate eine Entscheidung ist und der Token eine Faehigkeit: ein
            # absichtlich geschlossenes Gate darf diese Pruefung nicht
            # verdecken. Sonst nichts.
            env={"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin",
                 "MOLTRUST_NOTIFY": "on",
                 "MOLTRUST_NOTIFY_STATE_DIR": tmp},
            capture_output=True, text=True, timeout=120)

    # 70 ist MUTE_EXIT_CODE: notify bricht selbst ab, wenn es nicht melden
    # kann. Das ist hier der Befund, nicht ein Fehler der Pruefung — das Kind
    # schreibt seine Antwort vor dem Abbruch.
    if p.returncode not in (0, 70) or not p.stdout.strip():
        letzte = (p.stderr or "").strip().splitlines()[-1:] or ["?"]
        print(f"UNLESBAR: die Pruefung selbst lief nicht (rc={p.returncode}):",
              letzte[0][:160])
        print(1)
        return 1

    try:
        d = json.loads(p.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        print("UNLESBAR: keine Antwort aus dem Kindprozess")
        print(1)
        return 1

    beanstandungen = []
    if d["token_laenge"] == 0:
        beanstandungen.append(
            "TELEGRAM_BOT_TOKEN loest ohne Umgebung nicht auf")

    for kanal, w in sorted(d["kanaele"].items()):
        if w.get("chat_fehler"):
            beanstandungen.append(f"{kanal}: chat_id_for wirft "
                                  f"{w['chat_fehler']}")
            continue
        if w.get("chat_laenge", 0) == 0:
            beanstandungen.append(f"{kanal}: chat_id loest nicht auf")
        if not w["bis_zum_transport"]:
            beanstandungen.append(
                f"{kanal}: der Sendeweg bricht vor dem Transport ab "
                f"(Grund: {w.get('grund', '?')})")

    for z in beanstandungen:
        print("BEANSTANDET:", z)
    if not beanstandungen:
        eigene = [k for k, w in d["kanaele"].items() if w.get("eigene_id")]
        print(f"ok {len(d['kanaele'])} Kanaele erreichen ohne Umgebung den "
              f"Transport; eigener Chat fuer: "
              f"{', '.join(sorted(eigene)) or 'keinen'}")
    print(len(beanstandungen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
