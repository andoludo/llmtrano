"""Engine and session factory. Synchronous SQLAlchemy: callers in async code use a thread."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_sessionmaker(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)
