"""The few schema changes SQLite will not make on its own.

`init_db` builds the schema with `Base.metadata.create_all`, which creates missing
tables and nothing else: a new column on a table that already exists, or a NOT NULL that
needs to become nullable, is applied to a fresh database and silently skipped on the one
this app is already running on. Until Alembic lands, those two cases live here.

Everything below is idempotent and guarded by an inspection of the live schema, so it is
safe to run on every start, and a no-op on a database that is already current.
"""

import logging

from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.models.base import Base

log = logging.getLogger(__name__)

# (table, column, DDL type) for columns added to tables that already existed. ALTER TABLE
# ADD COLUMN is one of the few things SQLite does support outright.
ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("interviews", "autopilot_status", "VARCHAR(16) DEFAULT 'idle'"),
    ("interviews", "autopilot_step", "VARCHAR(32)"),
    ("interviews", "autopilot_error", "TEXT"),
    ("interview_documents", "source_url", "VARCHAR(1024)"),
]

# Columns whose NOT NULL has to be dropped. SQLite cannot alter a column in place, so
# these force a table rebuild - see `_rebuild`.
RELAXED_NOT_NULL: list[tuple[str, str]] = [
    ("interview_documents", "interview_id"),
    ("document_chunks", "interview_id"),
]


async def run(conn: AsyncConnection) -> None:
    if conn.dialect.name != "sqlite":
        # Postgres gets real migrations the day it gets used; guessing at ALTERs for a
        # database nobody is running would be worse than doing nothing.
        return

    existing = await _table_names(conn)

    for table, column, ddl in ADDED_COLUMNS:
        if table not in existing:
            continue
        if column in await _columns(conn, table):
            continue
        await conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}')
        log.info("Migrated: added %s.%s", table, column)

    for table, column in RELAXED_NOT_NULL:
        if table not in existing:
            continue
        if not await _is_not_null(conn, table, column):
            continue
        await _rebuild(conn, table)
        log.info("Migrated: %s.%s is now nullable", table, column)

    await _promote_knowledge_base(conn, existing)


async def _table_names(conn: AsyncConnection) -> set[str]:
    rows = await conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
    return {row[0] for row in rows.fetchall()}


async def _columns(conn: AsyncConnection, table: str) -> set[str]:
    rows = await conn.exec_driver_sql(f"PRAGMA table_info('{table}')")
    return {row[1] for row in rows.fetchall()}


async def _is_not_null(conn: AsyncConnection, table: str, column: str) -> bool:
    rows = await conn.exec_driver_sql(f"PRAGMA table_info('{table}')")
    for row in rows.fetchall():
        if row[1] == column:
            return bool(row[3])
    return False


async def _rebuild(conn: AsyncConnection, name: str) -> None:
    """Rebuild one table from the current model definition, carrying its rows across.

    The SQLite recipe for any column change: make a new table, copy, swap. The new DDL
    comes from SQLAlchemy's metadata rather than being written out by hand here, so this
    cannot drift from the model the way a hand-copied CREATE TABLE would.

    Only the columns present in BOTH the old table and the model are copied - a column
    the model has since dropped is left behind, and one it has since added takes its
    default. Foreign keys are not enforced on this connection (SQLite leaves
    `foreign_keys` off unless asked), so the drop-and-rename needs no guard.
    """
    table: Table = Base.metadata.tables[name]
    old = f"{name}__migrate_old"
    carried = sorted(await _columns(conn, name) & {c.name for c in table.columns})

    # An index keeps its name when its table is renamed, so the fresh CREATE would
    # collide with it. Drop them first; `table.create` re-creates the current set.
    rows = await conn.exec_driver_sql(f"PRAGMA index_list('{name}')")
    for row in rows.fetchall():
        index_name = row[1]
        if not index_name.startswith("sqlite_autoindex"):
            await conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{index_name}"')

    await conn.exec_driver_sql(f'ALTER TABLE "{name}" RENAME TO "{old}"')
    await conn.run_sync(table.create)
    columns = ", ".join(f'"{c}"' for c in carried)
    await conn.exec_driver_sql(
        f'INSERT INTO "{name}" ({columns}) SELECT {columns} FROM "{old}"'
    )
    await conn.exec_driver_sql(f'DROP TABLE "{old}"')


async def _promote_knowledge_base(conn: AsyncConnection, existing: set[str]) -> None:
    """Move per-interview knowledge-base documents to the company-wide scope.

    Knowledge base used to be uploaded once per interview alongside the job description.
    It is now one static company set (app/api/knowledge.py), which is what those
    documents were always meant to be - the same company facts retyped per position.
    Detaching them keeps them retrievable everywhere instead of stranding them on an
    interview page that no longer has a place to show them.

    Job-requirement documents are untouched: those genuinely belong to one interview.
    """
    if "interview_documents" not in existing:
        return
    result = await conn.execute(
        text(
            "UPDATE interview_documents SET interview_id = NULL "
            "WHERE doc_type = 'knowledge_base' AND interview_id IS NOT NULL"
        )
    )
    moved = result.rowcount or 0
    if not moved:
        return
    await conn.execute(
        text(
            "UPDATE document_chunks SET interview_id = NULL WHERE document_id IN "
            "(SELECT id FROM interview_documents WHERE doc_type = 'knowledge_base')"
        )
    )
    log.info(
        "Migrated: %d knowledge-base document(s) moved to the company-wide scope", moved
    )
