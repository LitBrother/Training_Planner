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
    kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if url == "sqlite://":
        kwargs["poolclass"] = StaticPool
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
    adds = [("running_sessions", "garmin_id", "VARCHAR"), ("running_sessions", "source", "VARCHAR"),
            ("running_sessions", "manual_lock", "BOOLEAN DEFAULT 0"),
            ("strength_sessions", "status", "VARCHAR DEFAULT 'planned'")]
    with engine.begin() as conn:
        for table, col, ddl in adds:
            if insp.has_table(table) and col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
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