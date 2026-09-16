"""Database session management for the unified data spine."""
from collections.abc import Iterator

import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True, future=True)

if settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns() -> None:
    """Bring an existing database up to the current mapping, additively.

    Only ever ADDs nullable/defaulted columns - it will not drop, rename or
    retype anything, so it cannot destroy data it does not understand. Columns
    it cannot add are logged and left alone rather than aborting startup.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue                                    # create_all just made it
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present or column.primary_key:
                continue
            ddl_type = column.type.compile(dialect=engine.dialect)
            default = ""
            if column.default is not None and getattr(column.default, "is_scalar", False):
                literal = column.default.arg
                if isinstance(literal, bool):
                    literal = int(literal) if settings.is_sqlite else str(literal).upper()
                default = f" DEFAULT {literal!r}" if isinstance(literal, str) else f" DEFAULT {literal}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl_type}{default}'
                    ))
                log.info("schema: added %s.%s", table.name, column.name)
            except Exception as exc:
                log.warning("schema: could not add %s.%s (%s)", table.name, column.name, exc)


def init_db() -> None:
    """Create tables, migrate additively, and promote timeseries tables on TimescaleDB."""
    from app import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    for index in models.PERFORMANCE_INDEXES:
        index.create(bind=engine, checkfirst=True)
    if settings.timescale_enabled and not settings.is_sqlite:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            for table, col in (("sensor_readings", "ts"), ("occupancy_daily", "date")):
                conn.execute(
                    text(
                        f"SELECT create_hypertable('{table}', '{col}', "
                        "if_not_exists => TRUE, migrate_data => TRUE)"
                    )
                )
