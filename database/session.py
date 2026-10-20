from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from models.orm import Base

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "adam_marathon.db"
_factory: Optional[sessionmaker[Session]] = None


def configure(url: str | None = None) -> None:
    global _factory
    if url is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DB_PATH}"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url == "sqlite://":
            kwargs["poolclass"] = StaticPool
    elif url.startswith("postgresql+psycopg://"):
        kwargs["connect_args"] = {"prepare_threshold": None}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite") and url != "sqlite://":
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.close()
    Base.metadata.create_all(engine)
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    adds = [("users", "auth_uid", "VARCHAR"), ("users", "email", "VARCHAR"),
            ("running_sessions", "garmin_id", "VARCHAR"), ("running_sessions", "source", "VARCHAR"),
            ("running_sessions", "manual_lock", "BOOLEAN DEFAULT 0"),
            ("running_sessions", "manual_duration_min", "FLOAT"),
            ("running_sessions", "perceived_effort_rpe", "INTEGER"),
            ("strength_sessions", "status", "VARCHAR DEFAULT 'planned'"),
            ("ai_adjustments", "created_by_user_id", "INTEGER")]
    with engine.begin() as conn:
        for table, col, ddl in adds:
            if insp.has_table(table) and col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        if insp.has_table("users"):
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_auth_uid ON users (auth_uid)"))
        if url.startswith("postgresql"):
            for table in Base.metadata.sorted_tables:
                conn.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))
    _factory = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session_scope():
    if _factory is None:
        configure()
    assert _factory is not None
    s = _factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()