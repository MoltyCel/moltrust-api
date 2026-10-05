"""Names and line numbers, never values — and a floor under the count.

The case: HEALTHCHECK_URL stood three times on 2026-10-04, twice as a literal
placeholder. The file is sourced, so the last assignment won silently.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "csd", ROOT / "scripts" / "check_secret_duplicates.py")
csd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csd)


def write(tmp_path, text):
    f = tmp_path / "secrets"
    f.write_text(text)
    return str(f)


def test_the_real_case(tmp_path):
    p = write(tmp_path, "A=1\nHEALTHCHECK_URL=https://hc-ping.com/DEINE-UUID\n"
                        "HEALTHCHECK_URL=https://hc-ping.com/DEINE-UUID\n"
                        "HEALTHCHECK_URL=https://hc-ping.com/2e769401\n")
    dupes, total = csd.duplicates(p)
    assert dupes == {"HEALTHCHECK_URL": [2, 3, 4]}
    assert total == 2


def test_export_and_indentation_count_as_assignments(tmp_path):
    p = write(tmp_path, "export K=1\n  K=2\nK =3\n")
    dupes, _ = csd.duplicates(p)
    assert dupes == {"K": [1, 2, 3]}


def test_comments_do_not_count(tmp_path):
    p = write(tmp_path, "K=1\n# K=2\n   # K=3\n")
    assert csd.duplicates(p)[0] == {}


def test_a_truncated_file_is_unreadable_not_clean(tmp_path, capsys, monkeypatch):
    import sys
    p = write(tmp_path, "A=1\nB=2\n")
    monkeypatch.setattr(sys, "argv", ["x", "--path", p])
    rc = csd.main()
    out = capsys.readouterr()
    assert rc == 2
    assert out.out.strip() == "-1"
    assert "abgeschnitten" in out.err


def test_values_never_reach_the_output(tmp_path, capsys, monkeypatch):
    import sys
    secret = "s3cr3t-value-that-must-not-appear"
    p = write(tmp_path, "".join(f"K{i}=x\n" for i in range(25))
              + f"DUP={secret}\nDUP={secret}\n")
    monkeypatch.setattr(sys, "argv", ["x", "--path", p])
    rc = csd.main()
    out = capsys.readouterr()
    assert rc == 1
    assert "DUP" in out.err and "Zeile 26, 27" in out.err
    assert secret not in out.err and secret not in out.out
