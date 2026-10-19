"""Database construction shared by the app and offline migration tools."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine, make_url

WRITE_MARKER = "mangaflow_transaction_has_writes"
TRACKED_MARKER = "mangaflow_tracks_writes"


def install_write_tracking(engine: Engine) -> None:
    """Track flushed/raw writes without treating PostgreSQL SELECT as a write.

    Unknown SQL is deliberately conservative. A rollback to a savepoint may
    keep the marker (safe false positive); only the outer boundary clears it.
    """
    @event.listens_for(engine, "engine_connect")
    def connected(connection):
        connection.info[TRACKED_MARKER] = True

    @event.listens_for(engine, "before_cursor_execute")
    def before_sql(connection, cursor, statement, parameters, context, executemany):
        compiled = context.compiled
        if compiled is not None and any(getattr(compiled, flag, False) for flag in ("isinsert", "isupdate", "isdelete")):
            connection.info[WRITE_MARKER] = True
            return
        operation = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        # WITH/SELECT may invoke data-modifying functions, so only SQLAlchemy
        # SELECT constructs and ordinary reads are supported as read-only here.
        if operation not in {"SELECT", "SHOW", "SET", "PRAGMA", "BEGIN", "SAVEPOINT", "RELEASE", "ROLLBACK"}:
            connection.info[WRITE_MARKER] = True

    @event.listens_for(engine, "commit")
    @event.listens_for(engine, "rollback")
    def ended(connection):
        # info transparently reconnects an invalidated DBAPI connection. During
        # rollback that would run BEFORE SQLAlchemy clears its transaction,
        # raising PendingRollbackError and hiding the original disconnect.
        # The dead connection's info is discarded already; clear only a live
        # one. Never reconnect from a transaction-boundary event.
        if not connection.invalidated and not connection.closed:
            connection.info.pop(WRITE_MARKER, None)


def create_database_engine(url: str | URL, **overrides) -> Engine:
    resolved = make_url(url)
    if resolved.drivername in {"postgres", "postgresql"}:
        resolved = resolved.set(drivername="postgresql+psycopg")
    options = {"pool_pre_ping": True}
    if resolved.get_backend_name() == "sqlite":
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    elif resolved.get_backend_name() == "postgresql":
        options.update(pool_size=12, max_overflow=8, pool_timeout=30,
                       connect_args={"options": "-c timezone=UTC", "connect_timeout": 10})
    else:
        raise ValueError("Only SQLite and PostgreSQL databases are supported")
    options.update(overrides)
    engine = create_engine(resolved, **options)
    if resolved.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_connection(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
    install_write_tracking(engine)
    return engine
