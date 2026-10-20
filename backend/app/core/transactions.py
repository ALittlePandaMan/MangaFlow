from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.db_engine import TRACKED_MARKER, WRITE_MARKER
from app.core.timing import measured, record_timing

logger = logging.getLogger(__name__)
T = TypeVar("T")


def has_pending_writes(db: Session) -> bool:
    if db.new or db.dirty or db.deleted:
        return True
    if not db.in_transaction():
        return False
    connection = db.connection()
    if connection.info.get(TRACKED_MARKER):
        return bool(connection.info.get(WRITE_MARKER))
    return True  # An untracked connection must not discard flushed writes.


def is_retryable_write(error: BaseException) -> bool:
    # Only known transaction aborts: serialization, deadlock, lock timeout.
    # A lost COMMIT acknowledgement must never cause automatic replay.
    return isinstance(error, OperationalError) and getattr(error.orig, "sqlstate", None) in {
        "40001", "40P01", "55P03",
    }


@measured("db.transaction_total")
def retry_write(  # noqa: UP047 -- runtime is Python 3.11
    db: Session, operation: Callable[[], T], *, attempts: int = 4, lock_timeout_ms: int = 1000,
) -> T:
    """Replay only a small DB-only unit, never inference, file I/O or a nested save.

    The callback must reapply all changes and must not commit or roll back.
    PostgreSQL row locks allow unrelated pages to save concurrently.
    """
    if attempts < 1 or lock_timeout_ms < 0:
        raise ValueError("attempts must be positive and lock_timeout_ms nonnegative")
    if db.info.get("retry_write_active"):
        raise ValueError("retry_write callbacks must not nest transactions")
    if has_pending_writes(db):
        raise ValueError("retry_write requires a clean session; move writes into operation")
    db.info["retry_write_active"] = True
    try:
        for attempt in range(attempts):
            db.rollback()  # Release the old snapshot and expire ORM identities.
            try:
                db.connection().exec_driver_sql(f"SET LOCAL lock_timeout = '{max(1, int(lock_timeout_ms))}ms'")
                result = operation()
                db.flush()
                db.commit()
                return result
            except Exception as exc:
                db.rollback()
                if not is_retryable_write(exc) or attempt + 1 == attempts:
                    raise
                logger.warning("Database contention; retrying transaction %s/%s (code=%s)",
                               attempt + 1, attempts, exc.orig.sqlstate)
                delay = 0.05 * 2**attempt
                record_timing("db.retry_backoff", delay)
                time.sleep(delay)
    finally:
        db.info.pop("retry_write_active", None)
    raise AssertionError("Transaction retry loop exhausted without returning or raising")
