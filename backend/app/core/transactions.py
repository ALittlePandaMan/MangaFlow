from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
T = TypeVar("T")


def has_pending_writes(db: Session) -> bool:
    if db.new or db.dirty or db.deleted:
        return True
    if not db.in_transaction():
        return False
    connection = db.connection()
    # A flush empties Session.dirty/new but its writes are NOT committed yet.
    # SQLite's legacy driver only opens a physical transaction on DML. For
    # other drivers, conservatively keep the existing transaction intact.
    return connection.dialect.name != "sqlite" or connection.connection.dbapi_connection.in_transaction


def is_sqlite_busy(error: BaseException) -> bool:
    if not isinstance(error, OperationalError) or not isinstance(error.orig, sqlite3.OperationalError):
        return False
    code = getattr(error.orig, "sqlite_errorcode", None)
    # Include extended codes such as BUSY_SNAPSHOT; retry the TRANSACTION,
    # never just its final COMMIT after SQLAlchemy has rolled back a flush.
    return (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} if code is not None else str(error.orig) in {
        "database is locked", "database table is locked",
    }


def retry_write(db: Session, operation: Callable[[], T], *, attempts: int = 4) -> T:  # noqa: UP047 -- runtime is Python 3.11
    """Replay a small, DB-only unit of work, not inference or file operations.

    The caller must start with no pending writes and re-apply ALL changes in
    operation. It must not commit, roll back, perform I/O, or call another
    retry_write. Acquiring the SQLite writer first avoids stale read upgrades.
    """
    if has_pending_writes(db):
        raise ValueError("retry_write requires a clean session; move writes into operation")
    for attempt in range(attempts):
        # Release any old read snapshot, and expire ORM identities before retry.
        db.rollback()
        connection = db.connection()
        sqlite = connection.dialect.name == "sqlite"
        old_timeout = None
        try:
            if sqlite:
                old_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
                connection.exec_driver_sql("PRAGMA busy_timeout=1000")
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                connection.exec_driver_sql(f"PRAGMA busy_timeout={int(old_timeout)}")
                old_timeout = None
            result = operation()
            db.flush()
            db.commit()
            return result
        except Exception as exc:
            if old_timeout is not None and not connection.closed:
                connection.exec_driver_sql(f"PRAGMA busy_timeout={int(old_timeout)}")
            db.rollback()
            if not is_sqlite_busy(exc) or attempt + 1 == attempts:
                raise
            logger.warning(
                "SQLite write contention; retrying transaction %s/%s (code=%s)",
                attempt + 1, attempts, getattr(exc.orig, "sqlite_errorname", "SQLITE_BUSY"),
            )
            time.sleep(0.05 * 2**attempt)
    raise ValueError("attempts must be positive")
