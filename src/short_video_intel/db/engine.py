from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


@lru_cache(maxsize=16)
def get_engine(database_url: str) -> Engine:
    """Create and cache a SQLAlchemy engine.

    SQLite paths are created on demand so the CLI can initialize a fresh
    workspace without a separate bootstrap step.
    """

    url = make_url(database_url)
    connect_args: dict[str, object] = {}

    if url.drivername.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            db_path = Path(url.database)
            db_path.parent.mkdir(parents=True, exist_ok=True)

    return create_engine(database_url, connect_args=connect_args)


@contextmanager
def get_session(database_url: str):
    """Yield a SQLAlchemy session with automatic rollback/close handling."""

    engine = get_engine(database_url)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    session: Session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
