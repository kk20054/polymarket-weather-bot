from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .db import connect, connect_readonly


ARCHIVABLE_BLOBS = {
    "forecast_members": "raw_json",
    "orderbooks": "raw_json",
}


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _persist_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with temp_path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)


def _cutoff_iso(before_days: int) -> str:
    days = max(1, int(before_days))
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _database_path(db_path: Path | None = None) -> Path:
    return Path(db_path or load_config().v3_db_path).resolve()


def _id_bounds(conn: sqlite3.Connection, table: str) -> tuple[int, int]:
    row = conn.execute(f"SELECT MIN(id), MAX(id) FROM {table}").fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def _cutoff_max_id(conn: sqlite3.Connection, table: str, cutoff: str) -> int:
    """Find the time boundary with indexed row lookups instead of scanning large JSON blobs."""
    low, high = _id_bounds(conn, table)
    best = 0
    while low and low <= high:
        midpoint = (low + high) // 2
        row = conn.execute(
            f"SELECT id, created_at FROM {table} WHERE id <= ? ORDER BY id DESC LIMIT 1",
            (midpoint,),
        ).fetchone()
        if row is None:
            low = midpoint + 1
            continue
        row_id = int(row["id"])
        if str(row["created_at"] or "") < cutoff:
            best = max(best, row_id)
            low = max(midpoint + 1, row_id + 1)
        else:
            high = min(midpoint - 1, row_id - 1)
    return best


def storage_audit(*, db_path: Path | None = None, before_days: int = 30) -> dict[str, Any]:
    path = _database_path(db_path)
    cutoff = _cutoff_iso(before_days)
    with connect_readonly(path) as conn:
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        tables: dict[str, Any] = {}
        for table, column in ARCHIVABLE_BLOBS.items():
            min_id, max_id = _id_bounds(conn, table)
            cutoff_id = _cutoff_max_id(conn, table, cutoff)
            span_rows = max(0, max_id - min_id + 1) if min_id else 0
            eligible_span = max(0, cutoff_id - min_id + 1) if cutoff_id and min_id else 0
            sample = conn.execute(
                f"SELECT {column} IS NOT NULL AS present, LENGTH({column}) AS size "
                f"FROM {table} WHERE id <= ? ORDER BY id DESC LIMIT 1000",
                (cutoff_id,),
            ).fetchall() if cutoff_id else []
            present_sizes = [int(row["size"] or 0) for row in sample if row["present"]]
            present_rate = (len(present_sizes) / len(sample)) if sample else 0.0
            average_bytes = (sum(present_sizes) / len(present_sizes)) if present_sizes else 0.0
            eligible_rows = int(round(eligible_span * present_rate))
            tables[table] = {
                "estimated_rows_total": span_rows,
                "cutoff_max_id": cutoff_id,
                "sample_rows": len(sample),
                "sample_blob_present_pct": round(present_rate * 100.0, 2),
                "sample_average_blob_bytes": int(round(average_bytes)),
                "eligible_rows": eligible_rows,
                "eligible_bytes": int(round(eligible_rows * average_bytes)),
            }
        return {
            "database": str(path),
            "database_bytes": path.stat().st_size,
            "cutoff": cutoff,
            "before_days": max(1, int(before_days)),
            "allocated_bytes": page_size * page_count,
            "reclaimable_freelist_bytes": page_size * freelist_count,
            "tables": tables,
            "estimated": True,
            "apply_note": "Archive first, then clear duplicate raw_json. Run offline VACUUM separately to shrink the file.",
        }


def _write_archive_batch(
    *,
    archive_dir: Path,
    table: str,
    column: str,
    rows: list[sqlite3.Row],
) -> dict[str, Any]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    first_id = int(rows[0]["id"])
    last_id = int(rows[-1]["id"])
    final_path = archive_dir / f"{table}-{first_id}-{last_id}.jsonl.gz"
    temp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    with gzip.open(temp_path, "wt", encoding="utf-8", newline="\n", compresslevel=6) as handle:
        for row in rows:
            raw_value = str(row[column])
            handle.write(json.dumps({
                "table": table,
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "column": column,
                "sha256": hashlib.sha256(raw_value.encode("utf-8")).hexdigest(),
                "value": raw_value,
            }, ensure_ascii=False, separators=(",", ":")) + "\n")
    with temp_path.open("r+b") as handle:
        os.fsync(handle.fileno())
    compressed_sha = _sha256_file(temp_path)
    temp_path.replace(final_path)
    return {
        "path": str(final_path),
        "rows": len(rows),
        "first_id": first_id,
        "last_id": last_id,
        "compressed_bytes": final_path.stat().st_size,
        "compressed_sha256": compressed_sha,
    }


def _clear_archived_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    ids: list[tuple[int]],
) -> int:
    before_changes = conn.total_changes
    conn.executemany(
        f"UPDATE {table} SET {column} = NULL WHERE id = ? AND {column} IS NOT NULL",
        ids,
    )
    return conn.total_changes - before_changes


def archive_redundant_blobs(
    *,
    db_path: Path | None = None,
    archive_root: Path | None = None,
    before_days: int = 30,
    batch_size: int = 10_000,
    apply: bool = False,
) -> dict[str, Any]:
    path = _database_path(db_path)
    cutoff = _cutoff_iso(before_days)
    if not apply:
        result = storage_audit(db_path=path, before_days=before_days)
        result["dry_run"] = True
        return result

    bounded_batch = max(100, min(int(batch_size), 50_000))
    root = Path(archive_root or path.parent / "archive" / "storage-maintenance").resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / run_id
    manifest_path = run_dir / "MANIFEST.json"
    manifest: dict[str, Any] = {
        "version": 1,
        "status": "in_progress",
        "database": str(path),
        "cutoff": cutoff,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
        "batches": [],
    }
    _persist_manifest(manifest_path, manifest)

    try:
        with connect(path) as conn:
            for table, column in ARCHIVABLE_BLOBS.items():
                archived_rows = 0
                archived_bytes = 0
                cutoff_id = _cutoff_max_id(conn, table, cutoff)
                last_seen_id = 0
                manifest["tables"][table] = {
                    "archived_rows": 0,
                    "archived_uncompressed_bytes": 0,
                }
                _persist_manifest(manifest_path, manifest)
                while True:
                    rows = conn.execute(
                        f"SELECT id, created_at, {column} FROM {table} "
                        f"WHERE id > ? AND id <= ? AND {column} IS NOT NULL AND created_at < ? "
                        f"ORDER BY id LIMIT ?",
                        (last_seen_id, cutoff_id, cutoff, bounded_batch),
                    ).fetchall()
                    if not rows:
                        break
                    batch = _write_archive_batch(
                        archive_dir=run_dir / table,
                        table=table,
                        column=column,
                        rows=rows,
                    )
                    batch["database_cleared"] = False
                    manifest["batches"].append(batch)
                    _persist_manifest(manifest_path, manifest)

                    ids = [(int(row["id"]),) for row in rows]
                    changed = _clear_archived_rows(
                        conn,
                        table=table,
                        column=column,
                        ids=ids,
                    )
                    if changed != len(rows):
                        conn.rollback()
                        raise RuntimeError(f"archive_update_count_mismatch:{table}:{changed}:{len(rows)}")
                    conn.commit()

                    batch["database_cleared"] = True
                    last_seen_id = int(rows[-1]["id"])
                    archived_rows += len(rows)
                    archived_bytes += sum(len(str(row[column]).encode("utf-8")) for row in rows)
                    manifest["tables"][table] = {
                        "archived_rows": archived_rows,
                        "archived_uncompressed_bytes": archived_bytes,
                    }
                    _persist_manifest(manifest_path, manifest)
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["failed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["error"] = f"{type(exc).__name__}:{exc}"
        _persist_manifest(manifest_path, manifest)
        raise

    manifest["status"] = "complete"
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    _persist_manifest(manifest_path, manifest)
    return {
        "dry_run": False,
        "database": str(path),
        "archive": str(run_dir),
        "manifest": str(manifest_path),
        "tables": manifest["tables"],
        "vacuum_required_for_file_shrink": True,
    }


def restore_blob_archive(*, db_path: Path | None = None, manifest_path: Path) -> dict[str, Any]:
    path = _database_path(db_path)
    manifest_file = Path(manifest_path).resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    restored = 0
    with connect(path) as conn:
        for batch in manifest.get("batches", []):
            archive_path = Path(batch["path"])
            if _sha256_file(archive_path) != batch["compressed_sha256"]:
                raise RuntimeError(f"archive_checksum_mismatch:{archive_path}")
            with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    record = json.loads(line)
                    table = str(record["table"])
                    column = str(record["column"])
                    if ARCHIVABLE_BLOBS.get(table) != column:
                        raise RuntimeError(f"archive_target_not_allowed:{table}:{column}")
                    value = str(record["value"])
                    if hashlib.sha256(value.encode("utf-8")).hexdigest() != record["sha256"]:
                        raise RuntimeError(f"archive_row_checksum_mismatch:{table}:{record['id']}")
                    cursor = conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE id = ? AND {column} IS NULL",
                        (value, int(record["id"])),
                    )
                    restored += max(0, int(cursor.rowcount))
            conn.commit()
    return {"database": str(path), "manifest": str(manifest_file), "restored_rows": restored}
