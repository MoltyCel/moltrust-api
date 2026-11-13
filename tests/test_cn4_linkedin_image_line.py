"""CN4: the LinkedIn draft message names the attached image.

Herkunft: Lars, Chat 03.11.2026 (ops CN4). The image is one of the two fixed
files from CN3; the message carries a line with its file name, inside the
first 80 characters that ~/selftest/telegram-sent.jsonl keeps of every send.
"""
import datetime

import pytest

from agents import linkedin_feedback as lf
from agents import linkedin_post, syndicate, voice_gate

UTC = datetime.timezone.utc
GOOD = ("Visa said on Monday that agents may now pay for travel bookings.\n\n"
        "A bank that cannot see who allowed a payment carries the loss.\n\n"
        "https://moltrust.ch/blog/a.html")


def fresh_item(**extra):
    when = (datetime.datetime.now(UTC) - datetime.timedelta(days=1))
    return {"title": "A", "link": "https://moltrust.ch/blog/a.html", "article_text": "x",
            "pub_date": when.strftime("%a, %d %b %Y %H:%M:%S +0000"), **extra}


@pytest.fixture
def tg(monkeypatch, tmp_path):
    seen = []

    def fake_send(text, **kw):
        seen.append({"text": text, **kw})
        return 7000 + len(seen)

    monkeypatch.setattr(syndicate.notify, "send_telegram_message", fake_send)
    monkeypatch.setattr(syndicate, "send_telegram",
                        lambda body, **kw: seen.append({"text": body, **kw}) or True)
    monkeypatch.setattr(syndicate.notify, "chat_id_for", lambda ch: "4711")
    monkeypatch.setattr(syndicate, "no_source_state_path", lambda: str(tmp_path / "n.json"))
    monkeypatch.setattr(linkedin_post, "pending_path", lambda: str(tmp_path / "p.json"))
    monkeypatch.setattr(lf, "versions_path", lambda: str(tmp_path / "v.jsonl"))
    monkeypatch.setattr(voice_gate, "scan", lambda *a, **k: {"violations": []})
    monkeypatch.setattr(voice_gate, "format_report", lambda s: "PASS")
    monkeypatch.setattr(syndicate, "fetch_article_text", lambda url: "x")
    from app import ablage
    monkeypatch.setattr(ablage, "ablegen", lambda *a, **k: None)
    return seen


@pytest.mark.parametrize("extra, name", [
    ({}, "moltrust-linkedin-1200x627.png"),
    ({"image_format": "square"}, "moltrust-linkedin-1200x1200.png"),
])
def test_draft_message_names_the_image(tg, extra, name):
    assert syndicate.deliver_linkedin(fresh_item(**extra), GOOD) is True
    msg = [m for m in tg if m.get("art") == "linkedin-entwurf"][0]["text"]
    assert f"\nBild: {name}\n" in msg
    # inside what the send log keeps (app/notify.py _record_sent, text[:80])
    assert f"Bild: {name}" in msg[:80]
    assert list(linkedin_post.load_pending().values())[0]["image"] == name


def test_blocked_draft_message_names_the_image_too(tg, monkeypatch):
    monkeypatch.setattr(voice_gate, "scan", lambda *a, **k: {"violations": ["g2g"]})
    assert syndicate.deliver_linkedin(fresh_item(), GOOD) is False
    msg = [m for m in tg if m.get("art") == "entwurf-blockiert"][0]["text"]
    assert "\nBild: moltrust-linkedin-1200x627.png\n" in msg


@pytest.mark.parametrize("image, name", [
    ("moltrust-linkedin-1200x1200.png", "moltrust-linkedin-1200x1200.png"),
    (None, "moltrust-linkedin-1200x627.png"),
])
def test_new_version_names_the_image(tg, monkeypatch, image, name):
    entry = {"text": GOOD, "title": "A", "source": "https://moltrust.ch/blog/a.html",
             "message_id": 10}
    if image:
        entry["image"] = image
    linkedin_post.save_pending({"k1": entry})
    monkeypatch.setattr(syndicate, "redraft_linkedin", lambda e, fb: (GOOD, "kürzer"))
    assert lf.redraft("k1", linkedin_post.load_pending()["k1"], "kürzer", reply_to=10) == 2
    assert f"\nBild: {name}\n" in tg[0]["text"]
