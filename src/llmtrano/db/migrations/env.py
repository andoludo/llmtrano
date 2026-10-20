"""Alembic environment: URL from Settings (DATABASE_URL), metadata from llmtrano.db.models."""

from alembic import context
from sqlalchemy import create_engine, pool

from llmtrano.db.models import Base
from llmtrano.settings import Settings

target_metadata = Base.metadata
# Tables that LangGraph's Postgres checkpointer creates and migrates itself (``setup()``): not Alembic's.
LANGGRAPH_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def include_object(obj: object, name: str | None, type_: str, reflected: bool, compare_to: object) -> bool:
    table = getattr(obj, "table", None)
    table_name = name if type_ == "table" else getattr(table, "name", None)
    return table_name not in LANGGRAPH_TABLES


def database_url() -> str:
    # A URL passed in by code (llmtrano.db.migrate or tests) wins over the environment.
    url = context.config.get_main_option("sqlalchemy.url")
    return url or Settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(), target_metadata=target_metadata, literal_binds=True, include_object=include_object
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
