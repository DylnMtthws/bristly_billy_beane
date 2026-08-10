#!/usr/bin/env python3
"""Daily database backup snapshot.

Runs daily via launchd (before the nightly refresh mutates data).

Uses SQLite's online backup API rather than a filesystem copy: the DB runs
in WAL mode and the Flask app may be writing concurrently, so a plain `cp`
can capture a torn image or miss un-checkpointed WAL contents. The backup API
produces a single, internally consistent snapshot file safely under load.

Snapshots are written to data/backups/sabermetrics-YYYYMMDD-HHMMSS.db and
old ones are pruned beyond a retention window (default 14 days).
"""

import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.utils.logging import setup_job_logging

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "sabermetrics.db"
BACKUP_DIR = PROJECT_DIR / "data" / "backups"
RETENTION_DAYS = 14


def make_snapshot(db_path: Path, backup_dir: Path, logger) -> Path:
    """Write a consistent snapshot of ``db_path`` into ``backup_dir``.

    Args:
        db_path: Source SQLite database.
        backup_dir: Destination directory for the snapshot file.
        logger: Configured job logger.

    Returns:
        Path to the snapshot file that was written.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"sabermetrics-{stamp}.db"

    # Read-only source connection; the online backup API streams a consistent
    # image even while the app holds write locks.
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    size_mb = dest.stat().st_size / (1024 * 1024)
    logger.info("Snapshot written: %s (%.1f MB)", dest.name, size_mb)
    return dest


def prune_old(backup_dir: Path, retention_days: int, logger) -> int:
    """Delete snapshots older than the retention window.

    Args:
        backup_dir: Directory holding snapshot files.
        retention_days: Age in days beyond which snapshots are removed.
        logger: Configured job logger.

    Returns:
        Number of snapshots deleted.
    """
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for snap in backup_dir.glob("sabermetrics-*.db"):
        if datetime.fromtimestamp(snap.stat().st_mtime) < cutoff:
            snap.unlink()
            removed += 1
            logger.info("Pruned old snapshot: %s", snap.name)
    return removed


def main() -> int:
    logger = setup_job_logging("backup")
    start = time.time()

    if not DB_PATH.exists():
        logger.error("Database not found at %s; nothing to back up", DB_PATH)
        return 1

    try:
        make_snapshot(DB_PATH, BACKUP_DIR, logger)
    except Exception as e:
        logger.error("Backup failed: %s", e)
        return 1

    removed = prune_old(BACKUP_DIR, RETENTION_DAYS, logger)
    remaining = len(list(BACKUP_DIR.glob("sabermetrics-*.db")))
    logger.info(
        "Backup complete in %.1fs — pruned=%d, retained=%d (window=%dd)",
        time.time() - start, removed, remaining, RETENTION_DAYS,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
