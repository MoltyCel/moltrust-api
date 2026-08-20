"""ensure_aws_marketplace_tables must actually perform the column rename.

The migration file and the ensure_ function are two independent sources for the
same schema, and only the migration file was ever exercised. In the sandbox the
rename had already been done by `psql -f 015`, so ensure_ ran against a table
that was already correct and its rename could not fail visibly. On production,
where ensure_ was the only thing that would perform it, it was a no-op:

    IF EXISTS (... column_name = 'event_id') THEN
        ALTER TABLE ... RENAME COLUMN event_id TO event_id;

— a blanket sns_message_id -> event_id replace had rewritten the source column
of the very statement meant to rename it.

This test puts the table back into the pre-migration shape inside a transaction,
runs the real function against the real database, and rolls back.
"""
import pytest

import app.aws_marketplace as awsmp


async def _columns(conn):
    return await conn.fetchval(
        "SELECT string_agg(column_name, ',' ORDER BY column_name) "
        "FROM information_schema.columns "
        "WHERE table_name = 'aws_marketplace_notifications' "
        "  AND column_name IN ('sns_message_id', 'event_id')")


@pytest.mark.asyncio
async def test_ensure_renames_the_legacy_column(app_with_lifespan):
    import app.main as m
    async with m.db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            # back to the pre-015 shape
            await conn.execute("ALTER TABLE aws_marketplace_notifications "
                               "RENAME COLUMN event_id TO sns_message_id")
            assert await _columns(conn) == "sns_message_id"

            await awsmp.ensure_aws_marketplace_tables(conn)

            assert await _columns(conn) == "event_id", (
                "ensure_ left the legacy column in place — the rename is a no-op")
        finally:
            await tr.rollback()


@pytest.mark.asyncio
async def test_ensure_is_idempotent_on_an_already_renamed_table(app_with_lifespan):
    """The path that always worked, pinned so the guard is not simply dropped."""
    import app.main as m
    async with m.db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            assert await _columns(conn) == "event_id"
            await awsmp.ensure_aws_marketplace_tables(conn)
            assert await _columns(conn) == "event_id"
        finally:
            await tr.rollback()


def test_no_self_assigning_rename_survives_in_the_source():
    """A rename whose source equals its target can never do anything."""
    import pathlib
    src = pathlib.Path(awsmp.__file__).read_text()
    for line in src.splitlines():
        if "RENAME COLUMN" in line:
            parts = line.split("RENAME COLUMN")[1].replace(";", "").split(" TO ")
            assert len(parts) == 2, line
            assert parts[0].strip() != parts[1].strip(), "self-assigning rename: " + line.strip()
