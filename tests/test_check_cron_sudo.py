"""The matcher behind c-cron-sudo-permitted.

The first version of that invariant compared binary paths and reported green on
the defect it was written for. These cases are that defect and the two ways the
comparison can go wrong in the other direction.
"""
import importlib.util
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_cron_sudo.py"
_spec = importlib.util.spec_from_file_location("check_cron_sudo", SRC)
ccs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccs)

# The NOPASSWD lines as `sudo -n -l` printed them on 2026-10-03.
SPECS = [
    "/usr/bin/systemctl restart moltproof",
    "/bin/systemctl restart moltstack",
    "/bin/systemctl status *",
    "/usr/local/sbin/moltstack-webinstall",
    "/usr/bin/install -m 644 -o www-data -g www-data "
    "/home/moltstack/agent-card-stage/agent-card.new.json "
    "/var/www/html/.well-known/agent-card.json",
]


def check(line):
    binary, args = ccs.normalise(line)
    return ccs.permitted(binary, args, SPECS)


def test_the_withdrawn_install_rule_is_a_finding():
    # The line that ran daily and failed daily. /usr/bin/install is in the
    # list; this argument list is not.
    assert check('/usr/bin/install -m 644 "$OUT" /var/www/html/registry-proof.json') is None


def test_the_replacement_is_permitted():
    assert check("-n /usr/local/sbin/moltstack-webinstall registry-proof.json")


def test_a_spec_without_arguments_permits_any_arguments():
    # sudoers semantics: no argument list means any arguments.
    assert check("/usr/local/sbin/moltstack-webinstall .well-known/agent-card.json")


def test_the_agent_card_install_is_still_permitted():
    assert check(
        "/usr/bin/install -m 644 -o www-data -g www-data "
        "/home/moltstack/agent-card-stage/agent-card.new.json "
        "/var/www/html/.well-known/agent-card.json"
    )


def test_a_wildcard_in_the_spec_matches():
    assert check("/bin/systemctl status moltstack")


def test_a_variable_argument_is_matched_as_a_wildcard():
    # Unknowable statically. Matching it loosely can hide a finding; refusing
    # it would produce a finding on every parameterised call, and a check that
    # cries wolf gets switched off.
    assert check("/bin/systemctl status $UNIT")


def test_sudo_own_options_are_stripped():
    binary, args = ccs.normalise("-n -u root /bin/systemctl restart moltstack")
    assert binary.endswith("/systemctl")
    assert args == ["restart", "moltstack"]


@pytest.mark.skipif(
    not pathlib.Path("/bin").is_symlink(), reason="/bin is not a symlink here"
)
def test_bin_and_usr_bin_name_the_same_command():
    # monitor.sh calls bare `systemctl`; the rule reads /bin/systemctl. sudo
    # treats those as one command, and the first run of this check did not.
    assert ccs.resolve("/bin/systemctl") == ccs.resolve("/usr/bin/systemctl")
    assert check("systemctl restart moltstack")


def test_a_command_nobody_granted_is_a_finding():
    assert check("/bin/systemctl restart nginx") is None
    assert check("/usr/bin/rm -rf /var/www/html") is None


def test_sudo_calls_reads_line_numbers_and_skips_comments(tmp_path):
    f = tmp_path / "x.sh"
    f.write_text(
        "#!/bin/bash\n"
        "# sudo systemctl restart nothing\n"
        "sudo -n /usr/local/sbin/moltstack-webinstall a.json\n"
        "echo done | sudo tee /tmp/x\n"
    )
    calls = ccs.sudo_calls(str(f))
    assert [n for n, _ in calls] == [3, 4]
    assert calls[0][1] == "-n /usr/local/sbin/moltstack-webinstall a.json"
    assert calls[1][1] == "tee /tmp/x"
