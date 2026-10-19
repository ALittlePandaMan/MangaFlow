from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from threading import RLock
from typing import TypeVar
from weakref import WeakKeyDictionary

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.db_engine import TRACKED_MARKER, WRITE_MARKER
from app.core.timing import measured, record_timing

logger = logging.getLogger(__name__)
T = TypeVar("T")
_writers: WeakKeyDictionary = WeakKeyDictionary()
_writers_lock = RLock()


def has_pending_writes(db: Session) -> bool:
    if db.new or db.dirty or db.deleted:
        return True
    if not db.in_transaction():
        return False
    connection = db.connection()
    if connection.dialect.name == "sqlite":
        return connection.connection.dbapi_connection.in_transaction
    if connection.info.get(TRACKED_MARKER):
        return bool(connection.info.get(WRITE_MARKER))
    return True  # Unknown engines must not discard flushed writes.


def is_retryable_write(error: BaseException) -> bool:
    # Only transaction-aborting conditions with known outcomes. Connection loss
    # at COMMIT is ambiguous and must NEVER repeat a request automatically.
    return is_sqlite_busy(error) or (
        isinstance(error, OperationalError)
        and getattr(error.orig, "sqlstate", None) in {"40001", "40P01", "55P03"}
    )


def is_sqlite_busy(error: BaseException) -> bool:
    if not isinstance(error, OperationalError) or not isinstance(error.orig, sqlite3.OperationalError):
        return False
    code = getattr(error.orig, "sqlite_errorcode", None)
    # Include extended codes such as BUSY_SNAPSHOT; retry the TRANSACTION,
    # never just its final COMMIT after SQLAlchemy has rolled back a flush.
    return (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} if code is not None else str(error.orig) in {
        "database is locked", "database table is locked",
    }


@measured("db.transaction_total")
def retry_write(  # noqa: UP047 -- runtime is Python 3.11
    db: Session, operation: Callable[[], T], *, attempts: int = 4, lock_timeout_ms: int = 1000,
) -> T:
    """Replay a small, DB-only unit of work, not inference or file operations.

    The caller must start with no pending writes and re-apply ALL changes in
    operation. It must not commit, roll back, perform I/O, or call another
    retry_write. Acquiring the SQLite writer first avoids stale read upgrades.
    """
    if attempts < 1 or lock_timeout_ms < 0:
        raise ValueError("attempts must be positive and lock_timeout_ms nonnegative")
    if db.info.get("retry_write_active"):
        raise ValueError("retry_write callbacks must not nest transactions")
    if has_pending_writes(db):
        raise ValueError("retry_write requires a clean session; move writes into operation")
    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        with _writers_lock:
            reservation = _writers.setdefault(getattr(bind, "engine", bind), RLock())
    else:
        reservation = None
    # Only DB callbacks use this reservation, never inference or file I/O.
    db.rollback()
    waiting = time.monotonic()
    acquired = reservation is None or reservation.acquire(timeout=attempts * lock_timeout_ms / 1000)
    record_timing("db.writer_queue", time.monotonic() - waiting)
    if not acquired:
        raise OperationalError("SQLite writer reservation timed out", {}, sqlite3.OperationalError("database is locked"))
    db.info["retry_write_active"] = True
    try:
        return _retry_write(db, operation, attempts=attempts, lock_timeout_ms=lock_timeout_ms)
    finally:
        db.info.pop("retry_write_active", None)
        if reservation is not None:
            reservation.release()


def _retry_write(db: Session, operation: Callable[[], T], *, attempts: int, lock_timeout_ms: int) -> T:  # noqa: UP047
    for attempt in range(attempts):
        # Release any old read snapshot, and expire ORM identities before retry.
        db.rollback()
        connection = db.connection()
        sqlite = connection.dialect.name == "sqlite"
        old_timeout = None
        try:
            if sqlite:
                old_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
                connection.exec_driver_sql(f"PRAGMA busy_timeout={int(lock_timeout_ms)}")
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                connection.exec_driver_sql(f"PRAGMA busy_timeout={int(old_timeout)}")
                old_timeout = None
            elif connection.dialect.name == "postgresql":
                connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{max(1, int(lock_timeout_ms))}ms'")
            result = operation()
            db.flush()
            db.commit()
            return result
        except Exception as exc:
            if old_timeout is not None and not connection.closed:
                connection.exec_driver_sql(f"PRAGMA busy_timeout={int(old_timeout)}")
            db.rollback()
            if not is_retryable_write(exc) or attempt + 1 == attempts:
                raise
            logger.warning(
                "Database write contention; retrying transaction %s/%s (code=%s)",
                attempt + 1, attempts, getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "sqlite_errorname", "SQLITE_BUSY"),
            )
            delay = 0.05 * 2**attempt
            record_timing("db.retry_backoff", delay)
            time.sleep(delay)
    raise ValueError("attempts must be positive")
