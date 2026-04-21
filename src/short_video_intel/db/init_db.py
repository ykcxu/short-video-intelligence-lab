from __future__ import annotations

import argparse

from . import models as _models  # noqa: F401  # ensure ORM classes are registered
from .base import Base
from .engine import get_engine


def create_db_and_tables(database_url: str) -> None:
    """Create the SQLite/Postgres-compatible schema via SQLAlchemy metadata."""

    engine = get_engine(database_url)
    Base.metadata.create_all(engine)


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the database schema.")
    parser.add_argument(
        "--database-url",
        default="sqlite:///data/app.db",
        help="SQLAlchemy database URL. Default: sqlite:///data/app.db",
    )
    args = parser.parse_args()
    create_db_and_tables(args.database_url)
    print(f"Database initialized: {args.database_url}")


if __name__ == "__main__":
    main()
