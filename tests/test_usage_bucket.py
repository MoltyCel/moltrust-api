"""The `usage` bucket (ops WM-2 point 4).

The client packages register a DID on their first call when the caller carries
none, each with its own platform value. Those values must land in `usage`, and
`usage` must count toward the public figure while staying a bucket of its own.
"""
import pathlib
import re

from app import registry_export

SQL = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sql"
       / "registry_export.sql").read_text()

USAGE_PLATFORMS = ("usage-x402", "usage-enforce", "usage-mcp")


def _bucket_map():
    body = SQL.split("bucket_map(platform, bucket) AS (VALUES", 1)[1].split("\n),", 1)[0]
    return dict(re.findall(r"\('([^']*)','([^']*)'\)", body))


def test_each_client_package_platform_lands_in_usage():
    mapping = _bucket_map()
    for platform in USAGE_PLATFORMS:
        assert mapping.get(platform) == "usage", platform


def test_usage_counts_and_own_test_still_does_not():
    assert "usage" in registry_export.COUNTED
    assert "own_test" not in registry_export.COUNTED


def test_no_platform_is_mapped_twice():
    body = SQL.split("bucket_map(platform, bucket) AS (VALUES", 1)[1].split("\n),", 1)[0]
    platforms = re.findall(r"\('([^']*)','[^']*'\)", body)
    assert len(platforms) == len(set(platforms))


def test_test_platform_stays_own_test():
    assert _bucket_map()["test"] == "own_test"
