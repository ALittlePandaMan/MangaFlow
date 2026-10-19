"""Offline, verified, empty-target database transfer (SQLite <-> PostgreSQL).

No worker startup, model loading, URL logging, overwrite, or source migration.
Callers must stop writers before the final cutover. SQLite rehearsals should
use a backup snapshot so copying does not depend on a live database's schema.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from itertools import islice
from pathlib import Path

from app.core.db_engine import create_database_engine
from app.core.migrations import HEAD, database_revision, upgrade_database
from cryptography.fernet import Fernet
from sqlalchemy import DateTime, MetaData, func, inspect, select, text
from sqlalchemy.engine import Engine


def backup_sqlite(source: Path, destination: Path) -> dict:
    """Use SQLite's online backup API, never copy a live DB without its WAL."""
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file() or source == destination:
        raise ValueError("Source must be an existing SQLite database and destination must differ")
    # Exclusive creation prevents accidentally replacing any user backup.
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as backup:
                original.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite backup integrity check failed")
                violations = backup.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise ValueError(f"SQLite backup has {len(violations)} foreign-key violations")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {"backup": str(destination), "bytes": destination.stat().st_size,
            "sha256": file_digest(destination), "integrity": "ok"}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    return value


def row_digest(digest, row):
    digest.update(json.dumps(normalized(dict(row)), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8"))
    digest.update(b"\n")


def table_digest(connection, table) -> dict:
    digest, count = hashlib.sha256(), 0
    query = select(table).order_by(*table.primary_key.columns).execution_options(yield_per=256)
    for row in connection.execute(query).mappings():
        row_digest(digest, row)
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


@contextmanager
def _read_snapshot(connection):
    previous = None
    try:
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        else:
            previous = connection.exec_driver_sql("PRAGMA query_only").scalar()
            connection.exec_driver_sql("PRAGMA query_only=ON")
            connection.exec_driver_sql("BEGIN")
        yield
    finally:
        connection.rollback()
        if previous is not None:
            connection.exec_driver_sql(f"PRAGMA query_only={int(previous)}")
            connection.rollback()


def copy_database(source: Engine, target: Engine, *, chunk_size: int = 256) -> dict:
    """Copy every mapped table, preserving IDs and comparing all row contents.

    Empty destination and current source schema are mandatory. All row writes,
    checks and sequence initialization are one destination transaction. Existing
    destination data is never truncated or upserted. Supports reverse transfer.
    """
    if source.url == target.url:
        raise ValueError("Source and target must differ")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if database_revision(source) != HEAD:
        raise ValueError("Upgrade an isolated source snapshot to the current schema before copying")
    with target.connect() as connection:
        tables = inspect(connection).get_table_names()
        for name in tables:
            if name != "alembic_version" and connection.execute(text(
                f"SELECT 1 FROM {connection.dialect.identifier_preparer.quote(name)} LIMIT 1",
            )).first():
                raise ValueError("Destination contains data; use a new empty database")
    upgrade_database(target)
    with source.connect() as original, _read_snapshot(original), target.begin() as destination:
        source_meta, target_meta = MetaData(), MetaData()
        source_meta.reflect(original)
        target_meta.reflect(destination)
        names = set(source_meta.tables) - {"alembic_version"}
        if names != set(target_meta.tables) - {"alembic_version"}:
            raise ValueError("Source and destination table sets differ")
        if destination.dialect.name == "postgresql":
            quoted = ", ".join(destination.dialect.identifier_preparer.quote(name) for name in sorted(names))
            destination.exec_driver_sql(f"LOCK TABLE {quoted} IN EXCLUSIVE MODE")
        for table in target_meta.sorted_tables:
            if table.name in names and destination.scalar(select(func.count()).select_from(table)):
                raise ValueError("Destination is no longer empty")
        report = {}
        for source_table in source_meta.sorted_tables:
            if source_table.name not in names:
                continue
            target_table = target_meta.tables[source_table.name]
            if set(source_table.columns.keys()) != set(target_table.columns.keys()):
                raise ValueError(f"Column mismatch: {source_table.name}")
            digest, count = hashlib.sha256(), 0
            rows = original.execute(select(source_table).order_by(*source_table.primary_key.columns)
                                    .execution_options(yield_per=chunk_size)).mappings()
            for batch in rows.partitions(chunk_size):
                values = []
                for row in batch:
                    row_digest(digest, row)
                    value = dict(row)
                    for column in target_table.columns:
                        item = value[column.name]
                        if isinstance(column.type, DateTime) and item is not None:
                            value[column.name] = item.replace(tzinfo=timezone.utc) if item.tzinfo is None else item.astimezone(timezone.utc)
                    values.append(value)
                destination.execute(target_table.insert(), values)
                count += len(values)
            expected = {"count": count, "sha256": digest.hexdigest()}
            actual = table_digest(destination, target_table)
            if actual != expected:
                raise ValueError(f"Content verification failed: {source_table.name}")
            report[source_table.name] = actual
        if destination.dialect.name == "postgresql":
            # Preserve the historical undo cursor and ensure the next insert
            # cannot collide with explicitly copied auto-increment IDs.
            sequence = destination.scalar(text("SELECT pg_get_serial_sequence('editor_history', 'id')"))
            maximum = destination.scalar(select(func.max(target_meta.tables["editor_history"].c.id)))
            if sequence:
                destination.execute(text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
                                    {"sequence": sequence, "value": maximum or 1, "called": maximum is not None})
        else:
            if destination.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise ValueError("Destination foreign-key validation failed")
    return {"revision": HEAD, "verified": True, "tables": report}


def validate_external_references(engine: Engine, root: Path, *, secret_key: bytes | None = None) -> dict:
    """Check assets and decryptability without printing credentials or contents."""
    root = root.resolve()
    assets: set[str] = set()
    encrypted: set[str] = set()

    # This is an offline, quiescent-tree verification. Resolving every ancestor
    # of every history asset repeats expensive Windows mount metadata calls.
    @lru_cache(maxsize=4096)
    def resolved_parent(path: Path) -> Path:
        return path.resolve()

    def verify_key(value):
        if value and value not in encrypted:
            if not secret_key:
                raise ValueError("Encrypted provider keys require the original secret key")
            Fernet(secret_key).decrypt(value.encode())
            encrypted.add(value)

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "encrypted_api_key":
                    verify_key(item)
                if key in {"artifacts", "input_assets"} and isinstance(item, dict):
                    visit(list(item))
                if key not in {"source_text", "translated_text", "text", "prompt", "raw_response"}:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value.startswith(("projects/", "fonts/")) and value not in assets:
            # Undo/revision snapshots repeat the same paths thousands of times.
            # Resolve each distinct reference once; still hash every unique
            # file below. This is significant on Windows-backed Docker mounts.
            candidate = root / value
            path = (candidate.resolve() if candidate.is_symlink() or candidate.name in {".", ".."}
                    else resolved_parent(candidate.parent) / candidate.name)
            if not path.is_relative_to(root):
                raise ValueError("Asset path escapes the data directory")
            assets.add(value)

    with engine.connect() as connection, _read_snapshot(connection):
        meta = MetaData()
        meta.reflect(connection)
        for table in meta.sorted_tables:
            if table.name == "alembic_version":
                continue
            for row in connection.execute(select(table).execution_options(yield_per=256)).mappings():
                visit(dict(row))
    missing = []
    manifest = hashlib.sha256()

    def hash_asset(relative):
        path = root / relative
        return relative, file_digest(path) if path.is_file() else None

    # File opening on Windows-backed Docker mounts is latency-bound. Only
    # plain paths cross these four I/O workers, AFTER the DB snapshot closes.
    # Bounded chunks avoid one future per asset on very large installations;
    # map preserves sorted order, hence the manifest remains byte-identical.
    references = iter(sorted(assets))
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="asset-verification") as pool:
        while chunk := list(islice(references, 128)):
            for relative, digest in pool.map(hash_asset, chunk):
                if digest is None:
                    missing.append(relative)
                else:
                    manifest.update(f"{relative}\0{digest}\n".encode())
    return {"assets_checked": len(assets), "asset_manifest_sha256": manifest.hexdigest(),
            "missing_assets": missing, "encrypted_keys_verified": len(encrypted), "verified": not missing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup-sqlite")
    backup.add_argument("source", type=Path)
    backup.add_argument("destination", type=Path)
    upgrade = commands.add_parser("upgrade")
    upgrade.add_argument("--url-env", required=True)
    copy = commands.add_parser("copy")
    copy.add_argument("--source-env", required=True)
    copy.add_argument("--target-env", required=True)
    verify = commands.add_parser("verify-assets")
    verify.add_argument("--url-env", required=True)
    verify.add_argument("--data-dir", required=True, type=Path)
    verify.add_argument("--secret-key-file", type=Path)
    for subparser in (backup, upgrade, copy, verify):
        subparser.add_argument("--report-file", type=Path, help="Write a new JSON evidence file (never overwrite)")
    args = parser.parse_args()
    engines = []

    def engine(name):
        value = os.environ.get(name)
        if not value:
            raise ValueError(f"Required URL environment variable is unset: {name}")
        result = create_database_engine(value)
        engines.append(result)
        return result

    try:
        if args.command == "backup-sqlite":
            result = backup_sqlite(args.source, args.destination)
        elif args.command == "upgrade":
            target = engine(args.url_env)
            upgrade_database(target)
            result = {"revision": database_revision(target)}
        elif args.command == "copy":
            result = copy_database(engine(args.source_env), engine(args.target_env))
        else:
            key = args.secret_key_file.read_bytes().strip() if args.secret_key_file else os.environ.get("MANGAFLOW_SECRET_KEY", "").encode()
            result = validate_external_references(engine(args.url_env), args.data_dir, secret_key=key)
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        if args.report_file:
            with args.report_file.open("x", encoding="utf-8") as report:
                report.write(serialized + "\n")
        print(serialized)
        if result.get("verified") is False:
            raise SystemExit(1)
    except Exception as error:
        # Driver exceptions may include credentials or row contents. CLI output
        # deliberately exposes only the class; in-process tests retain detail.
        print(json.dumps({"error": type(error).__name__, "message": "Migration failed; destination not activated"}))
        raise SystemExit(1) from None
    finally:
        for item in engines:
            item.dispose()


if __name__ == "__main__":
    main()
