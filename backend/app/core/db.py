"""Database session management for the unified data spine."""
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
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


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables, and promote the timeseries tables to hypertables on TimescaleDB."""
    from app import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=engine)
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
