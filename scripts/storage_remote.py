"""Trusted stdlib-only maintenance executed on the sole production writer.

No database content or signed URLs are returned in diagnostics.
"""

import fcntl
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.request

DATA = pathlib.Path("/data")
SCRATCH = pathlib.Path(os.environ.get("DECKLAB_STORAGE_SCRATCH", "/tmp"))
RESERVE = 1_000_000_000


class StorageError(Exception):
    pass


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def status():
    database = DATA / "sabermetrics.db"
    wal = database.with_name(database.name + "-wal")
    files = []
    for directory in [DATA / "release-backups", DATA / "backups"]:
        if directory.is_dir():
            for p in directory.iterdir():
                if (
                    p.is_file()
                    and not p.is_symlink()
                    and p.name.endswith((".db", ".db.gz"))
                ):
                    files.append(
                        {
                            "path": str(p),
                            "bytes": p.stat().st_size,
                            "modified": p.stat().st_mtime,
                        }
                    )
    usage = shutil.disk_usage(DATA)
    return {
        "free": usage.free,
        "total": usage.total,
        "used": usage.used,
        "database_bytes": database.stat().st_size,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "scratch_free": shutil.disk_usage(SCRATCH).free,
        "backups": files,
    }


def preflight(backup_bytes=None):
    report = status()
    if report["free"] < RESERVE:
        raise StorageError(
            f"Data volume has {report['free']} bytes free; {RESERVE} bytes required"
        )
    required = (
        backup_bytes
        if backup_bytes is not None
        else report["database_bytes"] + report["wal_bytes"]
    ) * 3 + 100_000_000
    if report["scratch_free"] < required:
        raise StorageError(
            f"Backup staging has {report['scratch_free']} bytes free; {required} bytes required"
        )
    return report


def checkpoint():
    if status()["free"] < RESERVE:
        raise StorageError("Restore data-volume headroom before checkpoint maintenance")
    conn = sqlite3.connect(DATA / "sabermetrics.db", timeout=1)
    try:
        passive = list(conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
        truncated = None
        if (
            passive[0] == 0
            and passive[1] == passive[2]
            and status()["wal_bytes"] > 64 * 1024 * 1024
        ):
            truncated = list(conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
        return {"passive": passive, "truncate": truncated, "storage": status()}
    finally:
        conn.close()


def verify_gzip(packed, output):
    with gzip.open(packed, "rb") as source, output.open("xb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    conn = sqlite3.connect(output.as_uri() + "?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise StorageError("Restored backup failed SQLite integrity validation")
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not tables:
            raise StorageError("Restored backup has no tables")
    finally:
        conn.close()
    return {
        "sha256": digest(output),
        "bytes": output.stat().st_size,
        "table_count": len(tables),
    }


def transfer(url, path, method):
    # Only short-lived signatures for this private bucket are accepted. The
    # controller holds the account credentials; production receives no S3 key.
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    if urlsplit(url).scheme != "https" or not (
        host.endswith(".tigris.dev")
        or host in {"t3.storage.dev", "fly.storage.tigris.dev"}
    ):
        raise StorageError("Unexpected backup transfer destination")
    if method == "PUT":
        with path.open("rb") as stream:
            req = urllib.request.Request(
                url,
                data=stream,
                method="PUT",
                headers={"Content-Length": str(path.stat().st_size)},
            )
            with urllib.request.urlopen(req, timeout=180) as response:
                response.read()
    else:
        with (
            urllib.request.urlopen(url, timeout=180) as response,
            path.open("xb") as target,
        ):
            shutil.copyfileobj(response, target, 1024 * 1024)


def publish_local(packed, local, identifier):
    """Publish only complete local archives; the supervisor also cleans on kill."""
    partial = local.parent / (".partial-" + identifier)
    try:
        with packed.open("rb") as source, partial.open("xb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        partial.chmod(0o600)
        partial.replace(local)
    finally:
        partial.unlink(missing_ok=True)


def backup(payload):
    if not re.fullmatch(r"[0-9a-f]{32}", payload.get("id", "")):
        raise StorageError("Invalid backup identity")
    preflight()
    legacy = payload.get("legacy")
    original = pathlib.Path(legacy) if legacy else DATA / "sabermetrics.db"
    if legacy and (
        original.parent not in {DATA / "release-backups", DATA / "backups"}
        or original.is_symlink()
        or not original.name.endswith((".db", ".db.gz"))
    ):
        raise StorageError("Legacy source is outside the backup allowlist")
    if legacy:
        original_bytes = original.stat().st_size
        if original.name.endswith(".gz"):
            original_bytes = 0
            with gzip.open(original, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    original_bytes += len(chunk)
                    preflight(original_bytes)
        preflight(original_bytes)
    before_hash = digest(original) if legacy else None
    with tempfile.TemporaryDirectory(
        prefix="storage-backup-", dir=SCRATCH
    ) as temporary:
        root = pathlib.Path(temporary)
        staged, packed = root / "snapshot.db", root / "snapshot.db.gz"
        if legacy and original.name.endswith(".gz"):
            shutil.copyfile(original, packed)
        else:
            if legacy:
                shutil.copyfile(original, staged)
            else:
                reader = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)
                writer = sqlite3.connect(staged)
                try:
                    deadline = time.monotonic() + 120

                    def progress(*_):
                        if time.monotonic() > deadline:
                            raise StorageError(
                                "Online backup exceeded its bounded execution time"
                            )

                    reader.backup(writer, pages=256, progress=progress, sleep=0.05)
                finally:
                    writer.close()
                    reader.close()
            with (
                staged.open("rb") as source,
                packed.open("xb") as target,
                gzip.GzipFile(
                    fileobj=target, mode="wb", compresslevel=1, mtime=0
                ) as zipped,
            ):
                shutil.copyfileobj(source, zipped, 1024 * 1024)
        # Do not overlap the snapshot, restored database and remote download.
        staged.unlink(missing_ok=True)
        restored = verify_gzip(packed, root / "verified.db")
        (root / "verified.db").unlink()
        packed_hash = digest(packed)
        transfer(payload["put_url"], packed, "PUT")
        transfer(payload["get_url"], root / "download.db.gz", "GET")
        if digest(root / "download.db.gz") != packed_hash:
            raise StorageError("Remote backup download checksum does not match")
        if legacy and digest(original) != before_hash:
            raise StorageError("Legacy backup changed during archival")
        local = (
            original
            if legacy
            else DATA / "release-backups" / (payload["id"] + ".db.gz")
        )
        local.parent.mkdir(exist_ok=True)
        if not legacy:
            if local.exists():
                raise StorageError("Refusing to replace an existing local backup")
            if shutil.disk_usage(DATA).free < RESERVE + packed.stat().st_size:
                raise StorageError("Insufficient local reserve after remote archival")
            publish_local(packed, local, payload["id"])
        receipt = {
            "id": payload["id"],
            "created": original.stat().st_mtime if legacy else time.time(),
            "object_key": payload["object_key"],
            "compressed_sha256": packed_hash,
            "compressed_bytes": packed.stat().st_size,
            "restore": restored,
            "local_path": str(local),
            "local_sha256": before_hash if legacy else packed_hash,
            "legacy": bool(legacy),
            "verified": True,
        }
        markers = DATA / "release-backups" / ".verified"
        markers.mkdir(mode=0o700, exist_ok=True, parents=True)
        (markers / (payload["id"] + ".json")).write_text(json.dumps(receipt))
        return receipt


def prune_local(payload):
    removed = []
    for receipt in payload["receipts"]:
        if not re.fullmatch(r"[0-9a-f]{32}", receipt.get("id", "")):
            raise StorageError("Invalid backup identity")
        marker = DATA / "release-backups" / ".verified" / (receipt["id"] + ".json")
        p = pathlib.Path(receipt["local_path"])
        if not marker.exists() and not p.exists():
            continue
        if (
            not marker.exists()
            or json.loads(marker.read_text()) != receipt
            or not receipt.get("verified")
        ):
            raise StorageError(
                "Local deletion lacks a matching verified archival receipt"
            )
        if (
            p.parent not in {DATA / "release-backups", DATA / "backups"}
            or p.is_symlink()
        ):
            raise StorageError("Local deletion is outside backup directories")
        if p.exists():
            if digest(p) != receipt["local_sha256"]:
                raise StorageError(
                    "Backup changed after archive verification; retaining local copy"
                )
            p.unlink()
            removed.append(str(p))
        marker.unlink()
    return {"removed": removed}


def restore(payload):
    preflight()
    with tempfile.TemporaryDirectory(
        prefix="storage-restore-", dir=SCRATCH
    ) as temporary:
        root = pathlib.Path(temporary)
        packed = root / "download.db.gz"
        transfer(payload["get_url"], packed, "GET")
        if digest(packed) != payload["compressed_sha256"]:
            raise StorageError("Restore download checksum mismatch")
        result = verify_gzip(packed, root / "restored.db")
        if result["sha256"] != payload["sha256"]:
            raise StorageError("Restored database checksum mismatch")
        return {"restored": True, **result}


def main(payload):
    os.umask(0o077)
    try:
        with pathlib.Path("/tmp/decklab-storage.lock").open("a") as lock:
            deadline = time.monotonic() + (120 if payload["action"] == "backup" else 0)
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise StorageError(
                            "Storage maintenance is busy; retry later"
                        ) from None
                    time.sleep(1)
            result = {
                "status": status,
                "preflight": preflight,
                "checkpoint": checkpoint,
            }.get(payload["action"])
            if result:
                output = result()
            else:
                output = {
                    "backup": backup,
                    "prune_local": prune_local,
                    "restore": restore,
                }[payload["action"]](payload)
        print(json.dumps({"ok": True, "result": output}))
    except StorageError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Storage operation failed; no completion confirmed",
                }
            )
        )
