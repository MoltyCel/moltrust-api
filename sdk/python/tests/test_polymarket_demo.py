"""The Polymarket demo runs offline end to end and every MolCheck record it writes verifies."""
import importlib.util
import json
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[1] / "examples" / "polymarket" / "demo.py"
spec = importlib.util.spec_from_file_location("polymarket_demo", _PATH)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_demo_runs_without_network_and_every_record_verifies(tmp_path, capsys):
    assert demo.main(["--out", str(tmp_path)]) == 0
    files = sorted(p.name for p in tmp_path.glob("0*.json"))
    assert files == [
        "001-allow-buy-in-band.json", "002-forbid-price-below-band.json",
        "003-forbid-market-not-in-mandate.json", "004-forbid-sell-not-allowed.json",
        "005-hold-hold-approved.json", "006-forbid-market-day-cap.json",
    ]
    sent = {f: json.loads((tmp_path / f).read_text())["sent"] for f in files}
    assert [f for f, s in sent.items() if s] == ["001-allow-buy-in-band.json",
                                                "005-hold-hold-approved.json"]
    assert "2 order(s) handed to the stub signer, 0 verify failure(s)" in capsys.readouterr().out


def test_demo_signs_nothing_itself():
    text = _PATH.read_text()
    for needle in ("clob.polymarket.com", "private_key", "eth_account", "web3"):
        assert needle not in text
