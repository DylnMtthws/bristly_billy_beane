"""Private object backups, retention and bounded production storage maintenance."""

import argparse
import base64
import gzip
import hashlib
import json
import os
import pathlib
import re
import shlex
import sqlite3
import subprocess
import tempfile
import time
import uuid
import zlib

APP = "dylnmtthws-decklab"
MACHINE = "2872537a9e3348"
PREFIX = "backups/"


class StorageError(Exception):
    """Safe diagnostic, never provider bodies, database content, or signatures."""


def retain_ids(receipts, now):
    """Keep one verified snapshot per hour/day/week plus all release pins."""
    rows = sorted(
        (r for r in receipts if r.get("verified")),
        key=lambda r: r["created"],
        reverse=True,
    )
    keep = set()
    for seconds, count in [(3600, 24), (86400, 7), (604800, 4)]:
        buckets = set()
        for r in rows:
            bucket = int(r["created"] // seconds)
            if bucket not in buckets and len(buckets) < count:
                buckets.add(bucket)
                keep.add(r["id"])
    for r in rows:
        if r.get("pin_until", 0) > now:
            keep.add(r["id"])
    return keep


def valid_receipt(r):
    return (
        isinstance(r, dict)
        and re.fullmatch(r"[0-9a-f]{32}", r.get("id", ""))
        and r.get("verified") is True
        and r.get("object_key") == PREFIX + r["id"] + ".db.gz"
        and re.fullmatch(r"[0-9a-f]{64}", r.get("compressed_sha256", ""))
        and re.fullmatch(r"[0-9a-f]{64}", r.get("restore", {}).get("sha256", ""))
        and isinstance(r.get("created"), (float, int))
    )


def supervisor_code(code, destination, timeout=600, local_partial=None):
    """The supervisor survives child timeouts and always removes staging files."""
    return f"""
import json,os,pathlib,shutil,subprocess,tempfile,sys
os.umask(0o077)
p=pathlib.Path({destination!r})
scratch=tempfile.mkdtemp(prefix='staging-',dir=p.parent)
try:
 with p.open('wb') as output:
  try:
   subprocess.run([sys.executable,'-'],input={code!r}.encode(),stdout=output,stderr=subprocess.DEVNULL,env={{**os.environ,'DECKLAB_STORAGE_SCRATCH':scratch}},timeout={timeout!r},check=True)
  except (subprocess.TimeoutExpired,subprocess.CalledProcessError):
   output.seek(0);output.truncate()
   output.write(json.dumps({{'ok':False,'error':'Background storage operation failed or timed out; staging cleaned'}}).encode())
finally:
 shutil.rmtree(scratch)
 if {local_partial!r} is not None:
  pathlib.Path({local_partial!r}).unlink(missing_ok=True)
"""


class Storage:
    def __init__(self, credentials=None, client=None, runner=subprocess.run):
        values = credentials or os.environ
        self.bucket = values["BUCKET_NAME"]
        endpoint = values.get("AWS_ENDPOINT_URL_S3", "https://fly.storage.tigris.dev")
        if endpoint not in {"https://fly.storage.tigris.dev", "https://t3.storage.dev"}:
            raise StorageError("Unexpected configured backup endpoint")
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                region_name=values.get("AWS_REGION", "auto"),
                aws_access_key_id=values["AWS_ACCESS_KEY_ID"],
                aws_secret_access_key=values["AWS_SECRET_ACCESS_KEY"],
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3},
                    connect_timeout=15,
                    read_timeout=180,
                ),
            )
        self.s3, self.runner = client, runner

    def remote(self, action, **payload):
        code = pathlib.Path(__file__).with_name("storage_remote.py").read_text()
        code += "\nmain(" + repr({"action": action, **payload}) + ")\n"
        if action in {"backup", "restore", "prune_local"}:
            return self.long_remote(
                code,
                payload.get("id") or uuid.uuid4().hex,
                local_copy=action == "backup" and not payload.get("legacy"),
            )
        return self.command(code)

    def command(self, code):
        # Fly Machines rejects large exec payloads; compress source and receipt data.
        encoded = base64.b64encode(zlib.compress(code.encode())).decode()
        launcher = (
            "import base64,zlib;exec(zlib.decompress(base64.b64decode("
            + repr(encoded)
            + ")))"
        )
        result = self.runner(
            [
                os.environ.get("FLYCTL", "flyctl"),
                "machine",
                "exec",
                MACHINE,
                "python -c " + shlex.quote(launcher),
                "-a",
                APP,
                "--json",
                "--timeout",
                "300",
            ],
            capture_output=True,
            timeout=330,
        )
        try:
            outer = json.loads(result.stdout)
            response = json.loads(outer["stdout"])
            if result.returncode or outer.get("exit_code", 0):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise StorageError(
                "Production storage command did not return a valid receipt"
            ) from None
        if not response.get("ok"):
            raise StorageError(response.get("error", "Storage operation failed"))
        return response["result"]

    def long_remote(self, code, identifier, local_copy=False):
        """Avoid the Machines exec HTTP timeout; poll one bounded, durable job."""
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise StorageError("Invalid storage operation identity")
        destination = "/tmp/decklab-storage-jobs/" + identifier + ".json"
        supervisor = supervisor_code(
            code,
            destination,
            local_partial=(
                "/data/release-backups/.partial-" + identifier if local_copy else None
            ),
        )
        start = f"""
import json,os,pathlib,subprocess
os.umask(0o077)
p=pathlib.Path({destination!r});p.parent.mkdir(exist_ok=True,mode=0o700)
pidfile=p.with_suffix('.pid')
if not p.exists():
 with p.open('xb') as output:
  process=subprocess.Popen(['python','-'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  pidfile.write_text(str(process.pid))
  process.stdin.write({supervisor!r}.encode());process.stdin.close()
print(json.dumps({{'ok':True,'result':{{'started':True}}}}))
"""
        try:
            self.command(start)
        except StorageError as exc:
            if str(exc) != "Production storage command did not return a valid receipt":
                raise
            # A lost acknowledgement does not mean the job failed to start.
        deadline = time.monotonic() + 650
        while time.monotonic() < deadline:
            poll = f"""
import json,pathlib,os
p=pathlib.Path({destination!r})
try:
 result=json.loads(p.read_text())
except (ValueError,FileNotFoundError):
 try:
  os.kill(int(p.with_suffix('.pid').read_text()),0)
  result={{'ok':True,'result':{{'pending':True}}}}
 except (ProcessLookupError,FileNotFoundError):
  result={{'ok':False,'error':'Background storage operation stopped without a receipt'}}
print(json.dumps(result))
"""
            try:
                result = self.command(poll)
            except StorageError as exc:
                if (
                    str(exc)
                    != "Production storage command did not return a valid receipt"
                ):
                    raise
                time.sleep(3)
                continue
            if not result.get("pending"):
                try:
                    self.command(
                        f"import pathlib,json; p=pathlib.Path({destination!r});p.unlink(missing_ok=True);p.with_suffix('.pid').unlink(missing_ok=True);print(json.dumps({{'ok':True,'result':{{}}}}))"
                    )
                except StorageError:
                    pass  # The verified operation succeeded; stale receipt cleanup can retry.
                return result
            time.sleep(3)
        raise StorageError(
            "Background storage operation exceeded its recovery window; inspect receipts before retrying"
        )

    def url(self, operation, key):
        return self.s3.generate_presigned_url(
            operation, Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=900
        )

    def receipts(self):
        rows = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=PREFIX
        ):
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith(".json"):
                    continue
                response = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])
                with response["Body"] as stream:
                    raw = stream.read(32769)
                if len(raw) > 32768:
                    raise StorageError("Backup manifest is oversized")
                r = json.loads(raw)
                if not valid_receipt(r) or obj["Key"] != PREFIX + r["id"] + ".json":
                    raise StorageError("Backup manifest failed identity validation")
                rows.append(r)
        return rows

    def backup(self, kind="hourly", legacy=None, release_sha=None):
        identifier = uuid.uuid4().hex
        key = PREFIX + identifier + ".db.gz"
        r = self.remote(
            "backup",
            id=identifier,
            object_key=key,
            put_url=self.url("put_object", key),
            get_url=self.url("get_object", key),
            legacy=legacy,
        )
        if not valid_receipt(r):
            raise StorageError("Backup returned an invalid identity")
        head = self.s3.head_object(Bucket=self.bucket, Key=key)
        if head["ContentLength"] != r["compressed_bytes"]:
            raise StorageError(
                "Uploaded backup size changed before recording its receipt"
            )
        r["object_etag"] = head["ETag"]
        r.update(
            kind=kind,
            archived_at=time.time(),
            release_sha=release_sha,
            pin_until=time.time() + 30 * 86400 if kind in {"release", "legacy"} else 0,
        )
        self.s3.put_object(
            Bucket=self.bucket,
            Key=PREFIX + identifier + ".json",
            Body=json.dumps(r).encode(),
            ContentType="application/json",
        )
        return r

    def retention(self):
        rows = self.receipts()
        # All data objects must still exist before deleting any corresponding local backup.
        for r in rows:
            head = self.s3.head_object(Bucket=self.bucket, Key=r["object_key"])
            if head["ContentLength"] != r["compressed_bytes"] or head["ETag"] != r.get(
                "object_etag"
            ):
                raise StorageError(
                    "Archived object is missing or changed; retaining local backups"
                )
        newest = sorted(rows, key=lambda r: r["created"], reverse=True)
        local_keep = {r["id"] for r in newest[:2]}
        # Remote markers contain the exact receipt as it existed before adding host metadata.
        marker_keys = {
            "id",
            "created",
            "object_key",
            "compressed_sha256",
            "compressed_bytes",
            "restore",
            "local_path",
            "local_sha256",
            "legacy",
            "verified",
        }
        present = {r["path"] for r in self.remote("status")["backups"]}
        local = self.remote(
            "prune_local",
            receipts=[
                {k: v for k, v in r.items() if k in marker_keys}
                for r in rows
                if r["id"] not in local_keep and r["local_path"] in present
            ],
        )
        keep = retain_ids(rows, time.time()) | local_keep
        deleted = []
        for r in rows:
            if r["id"] in keep:
                continue
            # Delete the manifest first: interruption leaves an orphan object, not a
            # valid-looking receipt for a deleted backup. Orphans are never restore candidates.
            self.s3.delete_object(Bucket=self.bucket, Key=PREFIX + r["id"] + ".json")
            self.s3.delete_object(Bucket=self.bucket, Key=r["object_key"])
            deleted.append(r["id"])
        known = {r["object_key"] for r in rows}
        orphans = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=PREFIX
        ):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if (
                    re.fullmatch(r"backups/[0-9a-f]{32}\.db\.gz", key)
                    and key not in known
                    and time.time() - obj["LastModified"].timestamp() > 48 * 3600
                ):
                    self.s3.delete_object(Bucket=self.bucket, Key=key)
                    orphans.append(key)
        return {
            "local": local,
            "remote_removed": deleted,
            "remote_retained": len(keep),
            "unreferenced_objects_removed": orphans,
        }

    def restore_test(self, receipt=None):
        rows = self.receipts()
        r = receipt or max(rows, key=lambda row: row["created"])
        if not valid_receipt(r):
            raise StorageError("Restore receipt failed validation")
        # A separate temporary database, never the production path. Nothing in the
        # backup is executed; only SQLite integrity and aggregate schema checks run.
        with tempfile.TemporaryDirectory(prefix="decklab-restore-") as temporary:
            root = pathlib.Path(temporary)
            packed, database = root / "backup.gz", root / "restored.db"
            self.s3.download_file(self.bucket, r["object_key"], str(packed))
            with packed.open("rb") as stream:
                if (
                    hashlib.file_digest(stream, "sha256").hexdigest()
                    != r["compressed_sha256"]
                ):
                    raise StorageError("Isolated restore download checksum mismatch")
            with gzip.open(packed, "rb") as source, database.open("xb") as target:
                import shutil

                shutil.copyfileobj(source, target, 1024 * 1024)
            with database.open("rb") as stream:
                if (
                    hashlib.file_digest(stream, "sha256").hexdigest()
                    != r["restore"]["sha256"]
                ):
                    raise StorageError("Isolated restored database checksum mismatch")
            conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                tables = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0]
                if integrity != "ok" or tables != r["restore"]["table_count"]:
                    raise StorageError("Isolated restore failed database validation")
            finally:
                conn.close()
        return {
            "verified": True,
            "backup_id": r["id"],
            "sha256": r["restore"]["sha256"],
            "table_count": tables,
            "completed_at": time.time(),
            "isolation": "temporary independent database; no production restore",
        }


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["status", "preflight", "checkpoint", "backup", "retention", "restore"],
    )
    parser.add_argument(
        "--kind", default="hourly", choices=["hourly", "release", "legacy"]
    )
    parser.add_argument("--legacy")
    parser.add_argument("--release-sha")
    args = parser.parse_args()
    try:
        storage = Storage()
        if args.command in {"status", "preflight", "checkpoint"}:
            result = storage.remote(args.command)
        elif args.command == "backup":
            result = storage.backup(args.kind, args.legacy, args.release_sha)
        elif args.command == "retention":
            result = storage.retention()
        else:
            result = storage.restore_test()
        print(json.dumps(result))
    except Exception as exc:
        raise SystemExit(
            str(exc)
            if isinstance(exc, StorageError)
            else "Storage provider operation failed; inspect private diagnostics"
        ) from None


if __name__ == "__main__":
    main()
