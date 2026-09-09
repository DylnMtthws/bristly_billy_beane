"""Behavioral backup, retention, restore and capacity failure tests."""

import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


remote = load("storage_remote")
host = load("storage_control")


@pytest.fixture
def environment(tmp_path, monkeypatch):
    data, scratch, bucket = (tmp_path / name for name in ["data", "scratch", "bucket"])
    for p in (data, scratch, bucket):
        p.mkdir()
    conn = sqlite3.connect(data / "sabermetrics.db")
    conn.execute("create table documents(id integer, title text)")
    conn.execute("insert into documents values(1,'Restore me')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(remote, "DATA", data)
    monkeypatch.setattr(remote, "SCRATCH", scratch)
    monkeypatch.setattr(remote, "RESERVE", 0)

    def transfer(key, path, method):
        if method == "PUT":
            shutil.copyfile(path, bucket / key)
        else:
            shutil.copyfile(bucket / key, path)

    monkeypatch.setattr(remote, "transfer", transfer)
    return data, scratch, bucket


def backup(identifier, legacy=None):
    return remote.backup(
        {
            "id": identifier,
            "object_key": "backups/" + identifier + ".db.gz",
            "put_url": identifier,
            "get_url": identifier,
            "legacy": legacy,
        }
    )


def test_backup_restores_data_and_round_trip_checksums(environment):
    data, scratch, bucket = environment
    r = backup("a" * 32)
    out = scratch / "restored.db"
    restored = remote.verify_gzip(bucket / ("a" * 32), out)
    assert restored["sha256"] == r["restore"]["sha256"]
    conn = sqlite3.connect(out)
    try:
        assert conn.execute("select title from documents").fetchone()[0] == "Restore me"
    finally:
        conn.close()
    assert (data / "sabermetrics.db").exists()


def test_failed_upload_preserves_original_and_creates_no_cleanup_receipt(
    environment, monkeypatch
):
    data, _, _ = environment
    original = (data / "sabermetrics.db").read_bytes()

    def fail(*args):
        raise OSError("network unavailable")

    monkeypatch.setattr(remote, "transfer", fail)
    with pytest.raises(OSError):
        backup("b" * 32)
    assert (data / "sabermetrics.db").read_bytes() == original
    assert not list((data / "release-backups").glob("*.gz"))


def test_corrupt_remote_download_prevents_local_cleanup(environment, monkeypatch):
    data, _, _ = environment

    def corrupt(key, path, method):
        if method == "GET":
            path.write_bytes(b"corrupt")

    monkeypatch.setattr(remote, "transfer", corrupt)
    with pytest.raises(remote.StorageError, match="checksum"):
        backup("c" * 32)
    assert not (data / "release-backups/.verified").exists()


def test_low_space_stops_before_backup(environment, monkeypatch):
    monkeypatch.setattr(remote, "RESERVE", 10**20)
    with pytest.raises(remote.StorageError, match="bytes required"):
        backup("d" * 32)


def test_legacy_cleanup_requires_verified_unchanged_archive(environment):
    data, _, _ = environment
    legacy = data / "backups/old.db"
    legacy.parent.mkdir()
    shutil.copyfile(data / "sabermetrics.db", legacy)
    r = backup("e" * 32, str(legacy))
    legacy.write_bytes(b"changed")
    with pytest.raises(remote.StorageError, match="changed"):
        remote.prune_local({"receipts": [r]})
    assert legacy.exists()


def test_twenty_cycles_keep_two_local_backups_and_cleanup_is_idempotent(environment):
    data, _, _ = environment
    receipts = []
    for n in range(20):
        receipts.append(backup(f"{n:032x}"))
        remote.prune_local({"receipts": receipts[:-2]})
        assert len(list((data / "release-backups").glob("*.gz"))) <= 2
    remote.prune_local({"receipts": receipts[:-2]})
    assert len(list((data / "release-backups").glob("*.gz"))) == 2


def test_retention_preserves_release_pins_and_hour_day_week_buckets():
    now = 100 * 86400
    rows = [
        {"id": str(n), "created": now - n * 3600, "verified": True}
        for n in range(24 * 60)
    ]
    rows.append(
        {"id": "pinned", "created": 0, "verified": True, "pin_until": now + 86400}
    )
    keep = host.retain_ids(rows, now)
    assert {str(n) for n in range(24)} <= keep
    assert "pinned" in keep
    assert len(keep) <= 24 + 7 + 4 + 1


class S3:
    def __init__(self, bucket):
        self.bucket = bucket

    def download_file(self, bucket, key, destination):
        shutil.copyfile(
            self.bucket / key.split("/")[-1].removesuffix(".db.gz"), destination
        )


def test_independent_restore_detects_corruption(environment, monkeypatch):
    _, _, bucket = environment
    r = backup("f" * 32)
    storage = host.Storage({"BUCKET_NAME": "private"}, client=S3(bucket))
    monkeypatch.setattr(storage, "receipts", lambda: [r])
    assert storage.restore_test()["verified"]
    (bucket / ("f" * 32)).write_bytes(b"corrupt")
    with pytest.raises(host.StorageError, match="checksum"):
        storage.restore_test()


def test_checkpoint_is_bounded_and_preserves_data(environment):
    data, _, _ = environment
    assert remote.checkpoint()["passive"][0] == 0
    conn = sqlite3.connect(data / "sabermetrics.db")
    try:
        assert conn.execute("select count(*) from documents").fetchone()[0] == 1
    finally:
        conn.close()


def test_background_backup_recovers_lost_ack_without_starting_twice(monkeypatch):
    storage = host.Storage({"BUCKET_NAME": "private"}, client=object())
    calls = []

    def command(code):
        calls.append(code)
        if len(calls) in {1, 3}:
            raise host.StorageError(
                "Production storage command did not return a valid receipt"
            )
        return {"verified": True}

    monkeypatch.setattr(storage, "command", command)
    assert storage.long_remote("safe code", "a" * 32) == {"verified": True}
    assert len(calls) == 3
    assert sum("subprocess.Popen" in code for code in calls) == 1


def test_background_backup_reports_failed_job(monkeypatch):
    storage = host.Storage({"BUCKET_NAME": "private"}, client=object())
    calls = []

    def command(code):
        calls.append(code)
        if len(calls) > 1:
            raise host.StorageError(
                "Background storage operation stopped without a receipt"
            )
        return {"started": True}

    monkeypatch.setattr(storage, "command", command)
    with pytest.raises(host.StorageError, match="stopped without a receipt"):
        storage.long_remote("safe code", "b" * 32)
    assert len(calls) == 2


def test_repeated_terminated_jobs_remove_staging(tmp_path):
    import json
    import subprocess
    import sys

    code = "import os,pathlib,time;pathlib.Path(os.environ['DECKLAB_STORAGE_SCRATCH'],'partial').write_bytes(b'x'*5000000);time.sleep(10)"
    for n in range(3):
        result = tmp_path / f"{n}.json"
        subprocess.run(
            [
                sys.executable,
                "-c",
                host.supervisor_code(code, str(result), timeout=0.2),
            ],
            check=True,
        )
        assert not list(tmp_path.glob("staging-*"))
        assert json.loads(result.read_text())["ok"] is False


def test_large_exec_payload_is_compressed_and_preserved():
    import json
    import shlex
    import subprocess
    import sys

    command_sizes = []

    def runner(args, **kwargs):
        command_sizes.append(len(args[4]))
        process = subprocess.run(
            [sys.executable, "-c", shlex.split(args[4])[2]],
            capture_output=True,
            check=False,
        )
        return subprocess.CompletedProcess(
            args,
            process.returncode,
            json.dumps({"stdout": process.stdout.decode()}).encode(),
        )

    storage = host.Storage({"BUCKET_NAME": "private"}, client=object(), runner=runner)
    code = (
        "import json\n"
        + ("# repeated receipt metadata\n" * 2000)
        + "print(json.dumps({'ok':True,'result':{'verified':True}}))"
    )
    assert storage.command(code) == {"verified": True}
    assert command_sizes[0] < 1000


def test_large_legacy_backup_requires_its_own_staging_capacity(
    environment, monkeypatch
):
    import collections

    data, scratch, _ = environment
    legacy = data / "backups/large.db"
    legacy.parent.mkdir()
    legacy.write_bytes(b"x" * 40_000_000)
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(
        remote.shutil,
        "disk_usage",
        lambda _: usage(200_000_000, 50_000_000, 150_000_000),
    )
    with pytest.raises(remote.StorageError, match="staging"):
        backup("1" * 32, str(legacy))
    assert legacy.exists()
    assert not list(scratch.iterdir())


def test_backup_drops_unneeded_staging_copies_before_download(environment, monkeypatch):
    import os

    data, _, _ = environment
    conn = sqlite3.connect(data / "sabermetrics.db")
    conn.execute("create table entropy(value blob)")
    conn.execute("insert into entropy values(?)", (os.urandom(2_000_000),))
    conn.commit()
    conn.close()
    transfer = remote.transfer

    def checked(key, path, method):
        if method == "GET":
            assert not (path.parent / "snapshot.db").exists()
            assert not (path.parent / "verified.db").exists()
        transfer(key, path, method)

    monkeypatch.setattr(remote, "transfer", checked)
    assert backup("2" * 32)["verified"]


def test_changed_archive_object_prevents_all_local_deletion(environment, monkeypatch):
    r = backup("3" * 32)
    r["object_etag"] = "original"

    class Changed:
        def head_object(self, **kwargs):
            return {"ContentLength": r["compressed_bytes"], "ETag": "changed"}

    storage = host.Storage({"BUCKET_NAME": "private"}, client=Changed())
    monkeypatch.setattr(storage, "receipts", lambda: [r])
    calls = []
    monkeypatch.setattr(storage, "remote", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(host.StorageError, match="retaining local"):
        storage.retention()
    assert calls == []
    assert Path(r["local_path"]).exists()
