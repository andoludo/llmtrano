"""The Postgres checkpointer of the workflow (``langgraph-checkpoint-postgres``), on a connection pool.

Postgres is the only database in every environment (CLAUDE.md rule 9); the checkpointer creates its own tables
with ``setup()``, which is idempotent.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from llmtrano.workflow.graph import make_serializer

POOL_MAX_SIZE = 10


def psycopg_conninfo(database_url: str) -> str:
    """SQLAlchemy's ``postgresql+psycopg://`` URL as a libpq connection string for psycopg."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@asynccontextmanager
async def postgres_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    pool = AsyncConnectionPool(
        psycopg_conninfo(database_url),
        max_size=POOL_MAX_SIZE,
        open=False,
        # what AsyncPostgresSaver.from_conn_string sets on its connection
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool, serde=make_serializer())  # type: ignore[arg-type]
        await saver.setup()
        yield saver
    finally:
        await pool.close()
