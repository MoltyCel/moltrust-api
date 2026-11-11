"""scripts/finance_watch.py (ops FIN-WATCH): triggers fire at the threshold, stay silent below it."""
import datetime as dt
import importlib.util
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("finance_watch", ROOT / "scripts" / "finance_watch.py")
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)

NOW = dt.datetime(2026, 11, 1, 7, 0, tzinfo=dt.timezone.utc)
TODAY = int(NOW.timestamp()) // 86400 * 86400


def cfg():
    c = fw.load_config()
    c["regulation"]["reviewed"] = "2026-10-24"
    return c


PLAIN_PAGE = {"name": "Some regulator", "url": "https://example.org/rules"}


def plain_cfg(**extra):
    """cfg() with one page the server reads itself, as the CFTC-style pages are."""
    c = cfg()
    c["regulation"]["pages"] = [{**PLAIN_PAGE, **extra}]
    return c


NY_URL = "https://gaming.ny.gov/proposed-rules"


def ny_html(*titles):
    return "".join(f'<a href="/f{i}.pdf" title="Download: {t}">x</a>'
                   for i, t in enumerate(titles))


NY_NOW = ("2026 Rule Review", "Cash 4 Life Game Repeal - Proposed",
          "Potential Rulemaking for Responsible Gaming (9 NYCRR Part 5404)")


def deliver(inbox, html, fetched="2026-11-01T06:30:00Z", status=200, url=NY_URL):
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "ny-proposed-rules.json").write_text(json.dumps(
        {"url": url, "fetched_at": fetched, "http_status": status, "html": html}))


def proto(slug, category, base=0.0, solana=0.0, name=None):
    return {"slug": slug, "name": name or slug, "category": category,
            "chainTvls": {"Base": base, "Solana": solana, "Arbitrum": 5e9}}


def protocols(zyfai=8_830_000.0, yieldseeker=930_000.0, aera=80_620_000.0):
    return [
        proto("zyfai", "AI Agents", base=zyfai, name="ZyFAI"),
        proto("krystal", "AI Agents", base=250_000, solana=20_000),
        proto("yieldseeker", "Yield Aggregator", base=yieldseeker, name="YieldSeeker"),
        proto("aera-v3", "Onchain Capital Allocator", base=aera, name="Aera V3"),
        proto("aave", "Lending", base=3e9, solana=1e9),
    ]


def x402_summary(base=25_000.0, solana=16_000.0):
    rows = [[TODAY - k * 86400, {"Base": {"x402": base}, "Solana": {"x402": solana},
                                 "Celo": {"x402": 100.0}}] for k in range(1, 40)]
    rows.append([TODAY, {"Base": {"x402": 9e9}}])  # today, incomplete
    return {"total30d": 1, "totalDataChartBreakdown": rows}


# ------------------------------------------------------------------ values

def test_agent_tvl_counts_category_and_extra_list_per_chain():
    t = fw.agent_tvl(protocols(), cfg())
    assert t["per_chain"] == {"Base": 8_830_000 + 250_000 + 930_000, "Solana": 20_000}
    assert t["total"] == 10_030_000
    assert {r["slug"]: r["origin"] for r in t["members"]} == {
        "zyfai": "Kategorie", "krystal": "Kategorie", "yieldseeker": "Zusatzliste"}
    assert t["largest"]["slug"] == "zyfai"
    assert t["missing"] == []


def test_other_chains_and_other_categories_do_not_count():
    t = fw.agent_tvl(protocols(), cfg())
    assert "aave" not in {r["slug"] for r in t["members"]}
    assert "aera-v3" not in {r["slug"] for r in t["members"]}


def test_extra_slug_missing_at_source_is_named_not_counted():
    t = fw.agent_tvl([p for p in protocols() if p["slug"] != "yieldseeker"], cfg())
    assert t["missing"] == ["yieldseeker"]
    assert t["total"] == 8_830_000 + 250_000 + 20_000


def test_x402_takes_30_complete_days_per_chain():
    v = fw.x402_volume(x402_summary(), NOW, ["Base", "Solana"])
    assert v["days"] == 30
    assert v["per_chain"] == {"Base": 750_000, "Solana": 480_000}
    assert v["all_chains"] == 750_000 + 480_000 + 3_000
    assert (v["from"], v["until"]) == ("2026-10-02", "2026-10-31")


def test_x402_without_days_is_an_error_not_a_zero():
    with pytest.raises(ValueError):
        fw.x402_volume({"totalDataChartBreakdown": []}, NOW, ["Base", "Solana"])


def test_mio_uses_german_separators():
    assert fw.mio(9_150_000) == "9,15 Mio. USD"
    assert fw.mio(1_234_567_890) == "1.234,57 Mio. USD"


# ---------------------------------------------------------------- triggers

def kinds(found):
    return [t["kind"] for t in found]


def test_silent_below_both_thresholds():
    assert fw.triggers(cfg(), fw.agent_tvl(protocols(), cfg()), {"new": []}, []) == []


def test_tvl_fires_at_the_threshold_for_base_and_solana_together():
    c = cfg()
    c["thresholds"]["agent_tvl_usd"] = 10_030_000      # exactly the sum of both chains
    found = fw.triggers(c, fw.agent_tvl(protocols(), c), {"new": []}, [])
    assert kinds(found) == ["tvl"]
    assert "Empfehlung:" in found[0]["text"]
    c["thresholds"]["agent_tvl_usd"] = 10_030_001
    assert fw.triggers(c, fw.agent_tvl(protocols(), c), {"new": []}, []) == []


def test_aum_fires_for_one_agent_at_25_million():
    c = cfg()
    found = fw.triggers(c, fw.agent_tvl(protocols(zyfai=25_000_000), c), {"new": []}, [])
    assert kinds(found) == ["aum"]
    assert "ZyFAI" in found[0]["text"]
    assert fw.triggers(c, fw.agent_tvl(protocols(zyfai=24_999_999), c), {"new": []}, []) == []


def test_autonomous_vault_without_ai_never_fires():
    c = cfg()
    big = protocols(aera=900_000_000)
    assert fw.triggers(c, fw.agent_tvl(big, c), {"new": []}, []) == []
    assert fw.autonomous_vaults(big, c)[0]["per_chain"]["Base"] == 900_000_000


def test_reviewed_rule_state_fires():
    c = cfg()
    c["regulation"]["agent_limits_rule"] = True
    assert kinds(fw.triggers(c, None, None, [])) == ["rule"]


def fr_fetchers(docs, texts):
    def fj(url, params):
        return {"results": docs}

    def ft(url):
        return texts[url]
    return fj, ft


def test_new_cftc_text_naming_agents_is_a_candidate_reviewed_and_loose_ones_are_not():
    docs = [
        {"document_number": "2026-11854", "title": "Prediction Markets", "type": "Proposed Rule",
         "publication_date": "2026-06-12", "html_url": "h1", "raw_text_url": "t1"},
        {"document_number": "2026-99999", "title": "Agent limits", "type": "Proposed Rule",
         "publication_date": "2026-10-30", "html_url": "h2", "raw_text_url": "t2"},
        {"document_number": "2026-88888", "title": "Swap data", "type": "Rule",
         "publication_date": "2026-10-29", "html_url": "h3", "raw_text_url": "t3"},
    ]
    texts = {"t1": "trading agents", "t2": "AI agents must keep limits. Autonomous agents too.",
             "t3": "an agent of the registrant; artificial intelligence"}
    fr = fw.federal_register(cfg(), "2026-10-24", *fr_fetchers(docs, texts))
    assert {d["number"]: d["hits"] for d in fr["new"]} == {"2026-99999": 2, "2026-88888": 0}
    assert kinds(fw.triggers(cfg(), None, fr, [])) == ["rule-candidate"]


def test_page_change_fires_only_with_agent_wording_and_never_on_first_read():
    c = plain_cfg()
    url = PLAIN_PAGE["url"]
    first = fw.watch_pages(c, {}, NOW, lambda u: "<p>Part 5404</p>")
    assert first[0]["first"] and fw.triggers(c, None, None, first) == []
    state = {"pages": {url: {"hash": first[0]["hash"], "since": "2026-10-24T07:00:00+00:00"}}}
    same = fw.watch_pages(c, state, NOW, lambda u: "<p>Part 5404</p><script>x=1</script>")
    assert not same[0]["changed"] and same[0]["since"].startswith("2026-10-24")
    plain = fw.watch_pages(c, state, NOW, lambda u: "<p>Part 5405</p>")
    assert plain[0]["changed"] and fw.triggers(c, None, None, plain) == []
    agents = fw.watch_pages(c, state, NOW, lambda u: "<p>AI agents placing wagers</p>")
    assert kinds(fw.triggers(c, None, None, agents)) == ["rule-candidate"]


# --------------------------------------------------------------------- run

@pytest.fixture()
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(fw, "HEARTBEAT", tmp_path / "hb.json")
    monkeypatch.setattr(fw, "DOWNLOADS", tmp_path / "Downloads")
    monkeypatch.setattr(fw, "INBOX", tmp_path / "inbox")
    deliver(tmp_path / "inbox", ny_html(*NY_NOW))
    sent = []
    fake = types.SimpleNamespace(
        ALERTS="alerts", STATS="stats",
        send_telegram=lambda text, channel, **kw: sent.append((channel, text)) or True)
    monkeypatch.setitem(sys.modules, "app.notify", fake)
    import app
    monkeypatch.setattr(app, "notify", fake, raising=False)
    # Since ops NOTIFY-3c the stats line goes into the daily report inbox;
    # recorded here as channel "eingang".
    inbox = types.SimpleNamespace(
        ablegen=lambda quelle, text, **kw: sent.append(("eingang", text)) or True)
    monkeypatch.setitem(sys.modules, "app.tagesbericht_eingang", inbox)
    monkeypatch.setattr(app, "tagesbericht_eingang", inbox, raising=False)

    def go(protos=None, fail=()):
        def fj(url, params=None):
            if url in fail:
                raise OSError("down")
            if url == fw.PROTOCOLS_URL:
                return protos or protocols()
            if url == fw.X402_URL:
                return x402_summary()
            return {"results": []}

        def ft(url):
            if url in fail:
                raise OSError("down")
            if url.endswith(".xml"):
                return "<rss><channel><item><title>Staff letter</title></item></channel></rss>"
            return "<p>Part 5404</p>"
        monkeypatch.setattr(fw, "fetch_json", fj)
        monkeypatch.setattr(fw, "fetch_text", ft)
        rc = fw.main([], now=NOW)
        return rc, json.loads((tmp_path / "hb.json").read_text())
    return go, sent, tmp_path


def test_quiet_month_writes_report_and_one_stats_line(run):
    go, sent, tmp = run
    rc, hb = go()
    assert rc == 0 and hb["status"] == "ok"
    assert [c for c, _ in sent] == ["eingang"]
    assert "nichts erreicht" in sent[0][1]
    text = (tmp / "Downloads" / "finance-watch-2026-11.md").read_text()
    for needle in ("Quelle", "abgerufen", "Allium Labs: nicht erhoben", "Crypto Briefing",
                   "Aera V3", "Base", "Solana", "zusammen"):
        assert needle in text
    state = json.loads((tmp / "state.json").read_text())
    assert state["fr_checked_until"] == "2026-11-01"


def test_threshold_month_sends_alert_for_lars(run):
    go, sent, _ = run
    rc, hb = go(protos=protocols(zyfai=120_000_000))
    assert hb["status"] == "alert"
    assert [c for c, _ in sent] == ["alerts", "eingang"]
    assert "Empfehlung:" in sent[0][1] and "100,00 Mio. USD" in sent[0][1]


def test_failed_source_gives_no_value_and_no_zero(run):
    go, sent, tmp = run
    rc, hb = go(fail={fw.PROTOCOLS_URL})
    assert hb["status"] == "partial"
    text = (tmp / "Downloads" / "finance-watch-2026-11.md").read_text()
    assert "nicht abrufbar" in text and "0,00 Mio. USD | 100" not in text
    assert [c for c, _ in sent] == ["eingang"]


def test_known_server_block_is_reported_not_a_fault_unknown_block_is():
    import urllib.error
    c = plain_cfg(blocked_from_server="die Seite sperrt die Adresse des Servers.")

    def refused(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    known = fw.watch_pages(c, {}, NOW, refused)
    assert known[0].get("blocked") and "error" not in known[0]
    del c["regulation"]["pages"][0]["blocked_from_server"]
    unknown = fw.watch_pages(c, {}, NOW, refused)
    assert "403" in unknown[0]["error"]


# ------------------------------------------------- NY Gaming Commission via Mac

def ny(state, inbox, now=NOW):
    return [p for p in fw.watch_pages(cfg(), state, now, lambda u: pytest.fail(
        f"server requested {u}"), inbox=inbox) if p["url"] == NY_URL][0]


def ny_state(p):
    return {"pages": fw.page_state([p], {})}


def test_server_never_requests_the_ny_page_and_records_a_baseline(tmp_path):
    deliver(tmp_path, ny_html(*NY_NOW))
    p = ny({}, tmp_path)
    assert p["via"] == "mac" and p["first"] and len(p["documents"]) == 3
    assert fw.triggers(cfg(), None, None, [p]) == []
    assert "Stand aufgenommen" in fw.page_lines([p], cfg()["regulation"])[0]


def test_quiet_month_same_documents_stays_silent(tmp_path):
    deliver(tmp_path, ny_html(*NY_NOW))
    st = ny_state(ny({}, tmp_path))
    deliver(tmp_path, ny_html(*reversed(NY_NOW)))
    p = ny(st, tmp_path)
    assert not p["changed"] and p["new_documents"] == []
    assert fw.triggers(cfg(), None, None, [p]) == []
    assert "keins neu" in fw.page_lines([p], cfg()["regulation"])[0]


def test_new_draft_on_agents_or_responsible_gaming_alerts(tmp_path):
    deliver(tmp_path, ny_html(*NY_NOW))
    st = ny_state(ny({}, tmp_path))
    deliver(tmp_path, ny_html(*NY_NOW, "Use of AI Agents by Patrons - Proposed"))
    found = fw.triggers(cfg(), None, None, [ny(st, tmp_path)])
    assert [t["kind"] for t in found] == ["rule-candidate"]
    assert "Use of AI Agents by Patrons" in found[0]["text"] and "Empfehlung:" in found[0]["text"]


def test_new_draft_on_racing_is_reported_not_alerted(tmp_path):
    deliver(tmp_path, ny_html(*NY_NOW))
    st = ny_state(ny({}, tmp_path))
    deliver(tmp_path, ny_html(*NY_NOW, "Claiming races in Harness Racing - Proposed"))
    p = ny(st, tmp_path)
    assert p["new_documents"] == ["Claiming races in Harness Racing - Proposed"]
    assert fw.triggers(cfg(), None, None, [p]) == []
    assert "Claiming races" in fw.page_lines([p], cfg()["regulation"])[0]


def test_negated_term_in_a_title_does_not_alert(tmp_path):
    deliver(tmp_path, ny_html(*NY_NOW))
    st = ny_state(ny({}, tmp_path))
    deliver(tmp_path, ny_html(*NY_NOW, "Lottery draws without automated terminals"))
    assert fw.triggers(cfg(), None, None, [ny(st, tmp_path)]) == []


@pytest.mark.parametrize("kw,needle", [
    (None, "keine Ablage"),
    ({"fetched": "2026-09-01T06:00:00Z"}, "Tage alt"),
    ({"status": 403}, "HTTP 403"),
    ({"url": "https://gaming.ny.gov/other"}, "andere Adresse"),
])
def test_unusable_inbox_is_a_source_error_not_a_zero(tmp_path, kw, needle):
    if kw is not None:
        deliver(tmp_path, ny_html(*NY_NOW), **kw)
    p = ny({}, tmp_path)
    assert needle in p["error"] and "documents" not in p


def test_inbox_page_without_recognisable_documents_is_an_error(tmp_path):
    deliver(tmp_path, "<html>redesigned page</html>")
    assert "kein Dokument erkannt" in ny({}, tmp_path)["error"]


def test_pages_only_appends_to_the_report_alerts_once(run, monkeypatch):
    go, sent, tmp = run
    go()                                     # monthly run records the baseline
    sent.clear()
    deliver(tmp / "inbox", ny_html(*NY_NOW, "Agentic wagering accounts - Proposed"),
            fetched="2026-11-01T09:00:00Z")
    later = NOW + dt.timedelta(hours=2)
    assert fw.main(["--pages-only"], now=later) == 0
    assert [c for c, _ in sent] == ["alerts"]
    text = (tmp / "Downloads" / "finance-watch-2026-11.md").read_text()
    assert "## Nachtrag" in text and "Agentic wagering accounts" in text
    sent.clear()
    assert fw.main(["--pages-only"], now=later) == 0
    assert sent == []


def test_monthly_report_names_the_mac_reading(run):
    go, sent, tmp = run
    rc, hb = go()
    assert hb["status"] == "ok"
    text = (tmp / "Downloads" / "finance-watch-2026-11.md").read_text()
    assert "vom Mac gelesen am 01.11.2026, 3 Dokumente" in text


def test_check_inbox_counts_missing_and_stale(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fw, "INBOX", tmp_path)
    assert fw.main(["--check-inbox"], now=NOW) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "1"
    deliver(tmp_path, ny_html(*NY_NOW))
    fw.main(["--check-inbox"], now=NOW)
    assert capsys.readouterr().out.strip().splitlines()[-1] == "0"
