"""The KEY guard: red when a write key reaches the MoltGuard build that signs with cast,
and the report never carries the value."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import moltguard_write_key_guard as G  # noqa: E402

VALUE = "ab" * 32
ENTRY = "/srv/moltguard/dist/index.js"


def world(tmp_path, *, environ=b"", dotenv="", cast=True, running=True):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    if running:
        p = proc / "4242"
        p.mkdir()
        (p / "cmdline").write_bytes(b"/usr/bin/node\0" + ENTRY.encode() + b"\0")
        (p / "environ").write_bytes(b"PATH=/usr/bin\0NODE_ENV=production\0" + environ)
    other = proc / "77"
    other.mkdir()
    (other / "cmdline").write_bytes(b"/usr/bin/node\0dist/index.js\0")
    (other / "environ").write_bytes(b"BASE_WRITE_KEY=" + VALUE.encode() + b"\0")
    env = tmp_path / ".env"
    env.write_text(dotenv)
    skill = tmp_path / "skill.js"
    skill.write_text("const cmd = 'cast send --private-key ' + BASE_KEY;" if cast else "wallet.sendTransaction(tx)")
    return dict(proc=str(proc), entry=ENTRY, dotenv=str(env), dist_skill=str(skill))


def check(code, lines, want):
    out = "\n".join(lines)
    assert VALUE not in out and VALUE[:8] not in out
    assert code == want, out
    return out


def test_green_when_no_key_anywhere(tmp_path):
    out = check(*G.run(**world(tmp_path, dotenv="BASE_WRITE_KEY=\nNODE_ENV=production\n")), 0)
    assert out.endswith("cast-Pfad ruht")


def test_red_when_the_running_process_has_the_key(tmp_path):
    out = check(*G.run(**world(tmp_path, environ=b"BASE_WRITE_KEY=" + VALUE.encode() + b"\0")), 1)
    assert "im laufenden MoltGuard-Prozess 4242 gesetzt" in out
    assert "der alte cast-Weg" in out and "scharf" in out


def test_red_when_dotenv_has_the_key_in_any_spelling(tmp_path):
    for line in (f"BASE_WRITE_KEY={VALUE}", f"export BASE_WRITE_KEY=\"0x{VALUE}\"", f"  BASE_WRITE_KEY = '{VALUE}' # x"):
        out = check(*G.run(**world(tmp_path / str(abs(hash(line))), dotenv=line + "\n")), 1)
        assert ".env gesetzt (laedt dotenv beim Start)" in out


def test_empty_quoted_and_commented_lines_are_no_key(tmp_path):
    env = "# BASE_WRITE_KEY=" + VALUE + "\nBASE_WRITE_KEY=\"\"\nBASE_WRITE_KEY= # leer\n"
    check(*G.run(**world(tmp_path, dotenv=env)), 0)


def test_only_the_moltguard_process_counts(tmp_path):
    # pid 77 has the key but runs another dist/index.js; it is not MoltGuard.
    out = check(*G.run(**world(tmp_path)), 0)
    assert "prozess 77" not in out


def test_no_running_process_is_unreadable(tmp_path):
    out = check(*G.run(**world(tmp_path, running=False)), 2)
    assert out.startswith("UNREADABLE")


def test_retires_once_the_cast_path_is_gone(tmp_path):
    out = check(*G.run(**world(tmp_path, cast=False, environ=b"BASE_WRITE_KEY=" + VALUE.encode() + b"\0")), 0)
    assert "entfaellt" in out
