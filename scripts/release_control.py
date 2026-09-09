"""Trusted release controller: promote a tested CI image; never rebuild on deploy."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import time
import tomllib
import urllib.request
from datetime import datetime
from pathlib import Path

REPOSITORY = "DylnMtthws/commander-deck-engine"
APP = "dylnmtthws-decklab"
CONFIG = Path("fly.production.toml")
FLY_VERSION = "0.4.101"
FLY_ARCHIVE_SHA = "81ada177239513e12e825c525290ad4de91f55a1c287d654876b6eadbb28b7f6"


class ReleaseError(Exception):
    """Safe release diagnostic; never contains provider output or credentials."""


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def maintenance_authorized(live_sha, target_sha):
    """A one-release operator exception, bound to both exact immutable commits."""
    return (
        valid_sha(live_sha)
        and valid_sha(target_sha)
        and live_sha != target_sha
        and os.environ.get("RELEASE_MAINTENANCE_BASE_SHA") == live_sha
        and os.environ.get("RELEASE_MAINTENANCE_HEAD_SHA") == target_sha
    )


def archived_backup(sha):
    from storage_control import Storage, StorageError

    try:
        storage = Storage()
        storage.remote("preflight")
        receipt = storage.backup("release", release_sha=sha)
        storage.retention()
        return receipt
    except StorageError as exc:
        raise ReleaseError("Production storage: " + str(exc)) from None
    except Exception:
        raise ReleaseError(
            "Private backup could not be verified; production was not replaced"
        ) from None


def execute(args, *, timeout=120, stdin=None):
    try:
        return subprocess.run(
            args, input=stdin, check=True, capture_output=True, timeout=timeout
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(
            "Release command failed; production promotion stopped"
        ) from exc


def gh(path):
    return json.loads(execute(["gh", "api", "repos/" + REPOSITORY + path]))


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def validate_provenance(run, main_sha, repository_id):
    require(
        run.get("status") == "completed" and run.get("conclusion") == "success",
        "Selected CI run did not complete successfully",
    )
    require(
        run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("path") == ".github/workflows/ci.yml",
        "Select a main CI push run",
    )
    require(
        run.get("repository", {}).get("id") == repository_id
        and run.get("head_repository", {}).get("id") == repository_id,
        "CI run belongs to a different repository",
    )
    require(
        valid_sha(main_sha) and run.get("head_sha") == main_sha,
        "Main moved or selected release is stale; use its current passing CI run",
    )


def validate_bundle(folder, *, expected_sha=None, run=None):
    manifest = json.loads((folder / "manifest.json").read_text())
    require(
        manifest.get("schema_version") == 1
        and manifest.get("repository") == REPOSITORY,
        "Invalid release manifest",
    )
    require(valid_sha(manifest.get("sha")), "Invalid release SHA")
    if expected_sha:
        require(manifest["sha"] == expected_sha, "Release SHA mismatch")
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", manifest.get("image_id", "")),
        "Invalid release image identity",
    )
    require(
        manifest.get("image_sha256") == file_sha(folder / "image.tar.gz"),
        "Release image archive checksum mismatch",
    )
    require(
        manifest.get("config_sha256") == file_sha(CONFIG),
        "Production configuration changed after the image was tested",
    )
    if run:
        require(
            manifest.get("run_id") == run["id"]
            and manifest.get("run_attempt") == run["run_attempt"],
            "CI attempt mismatch",
        )
    config = tomllib.loads(CONFIG.read_text())
    require(
        config.get("app") == APP
        and config.get("mounts")
        == [{"source": "decklab_data", "destination": "/data"}],
        "Unexpected production app or mount",
    )
    return manifest


def bundle(folder):
    require(
        os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main",
        "Only main CI can package a release",
    )
    sha = os.environ.get("GITHUB_SHA", "")
    require(valid_sha(sha), "Missing CI commit identity")
    image = json.loads(execute(["docker", "image", "inspect", "decklab-ci"]))[0]
    require(
        image["Architecture"] == "amd64"
        and image["Config"]["Labels"].get("org.opencontainers.image.revision") == sha,
        "CI image is not the expected build",
    )
    folder.mkdir(exist_ok=False)
    image_tar = folder / "image.tar"
    execute(["docker", "save", "--output", str(image_tar), image["Id"]], timeout=180)
    with (
        image_tar.open("rb") as source,
        (folder / "image.tar.gz").open("wb") as target,
        gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed,
    ):
        shutil.copyfileobj(source, compressed)
    image_tar.unlink()
    manifest = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "sha": sha,
        "image_id": image["Id"],
        "image_sha256": file_sha(folder / "image.tar.gz"),
        "config_sha256": file_sha(CONFIG),
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def get_run(run_id):
    require(str(run_id).isdigit(), "CI run ID must be numeric")
    repository = gh("")
    run = gh("/actions/runs/" + str(run_id))
    main_sha = gh("/git/ref/heads/main")["object"]["sha"]
    validate_provenance(run, main_sha, repository["id"])
    return run


def load_image(folder, manifest):
    execute(["docker", "load", "--input", str(folder / "image.tar.gz")], timeout=180)
    image = json.loads(execute(["docker", "image", "inspect", manifest["image_id"]]))[0]
    require(
        image["Id"] == manifest["image_id"] and image["Architecture"] == "amd64",
        "Loaded image differs from CI image",
    )
    require(
        image["Config"]["Labels"].get("org.opencontainers.image.revision")
        == manifest["sha"],
        "Loaded image commit identity mismatch",
    )


def prepare(run_id, folder):
    run = get_run(run_id)
    name = f"release-{run['head_sha']}-{run['run_attempt']}"
    folder.mkdir(exist_ok=False)
    execute(
        [
            "gh",
            "run",
            "download",
            str(run["id"]),
            "--repo",
            REPOSITORY,
            "--name",
            name,
            "--dir",
            str(folder),
        ],
        timeout=240,
    )
    manifest = validate_bundle(folder, expected_sha=run["head_sha"], run=run)
    load_image(folder, manifest)
    # This job has no production credential. Candidate code runs only offline.
    for script in (
        "smoke_password_recovery.py",
        "smoke_issue_feedback.py",
        "smoke_deck_lab.py",
    ):
        execute(
            [
                "docker",
                "run",
                "--rm",
                "--interactive",
                "--network",
                "none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=128",
                "--memory=1g",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                "--workdir",
                "/tmp",
                manifest["image_id"],
                "python",
                "-I",
                "-",
            ],
            stdin=(Path("scripts") / script).read_bytes(),
            timeout=120,
        )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text(
            f"## Release awaiting approval\n\nCommit: `{manifest['sha']}`\n\n"
            f"Tested image: `{manifest['image_id']}`\n\nCI: https://github.com/{REPOSITORY}/actions/runs/{run['id']}\n\n"
            "Approve the production environment to deploy this exact tested image.\n"
        )


def validate_machine(machines, expected_machine, expected_volume):
    require(
        bool(re.fullmatch(r"[a-z0-9]+", expected_machine))
        and bool(re.fullmatch(r"vol_[a-z0-9]+", expected_volume)),
        "Production identity is not configured",
    )
    require(len(machines) == 1, "Production must have exactly one machine")
    machine = machines[0]
    require(
        machine["id"] == expected_machine and machine["state"] == "started",
        "Unexpected or unhealthy production machine",
    )
    mounts = machine["config"].get("mounts", [])
    require(
        len(mounts) == 1
        and mounts[0].get("volume") == expected_volume
        and mounts[0].get("path") == "/data",
        "Production volume identity changed",
    )
    return machine


def release_paths_safe(paths):
    # Initial gate covers feedback UI iterations. Backend/schema/dependency and
    # release infrastructure changes require the separate migration/rollout runbook.
    return bool(paths) and all(
        path.startswith(
            (
                "src/sabermetrics/ui/static/",
                "src/sabermetrics/ui/templates/",
                "tests/",
                "docs/",
            )
        )
        and not any(part in {"auth", "admin"} for part in Path(path).parts)
        and not any(
            word in Path(path).name.lower()
            for word in ("password", "login", "admin", "auth")
        )
        for path in paths
    )


def fly_json(*args):
    return json.loads(execute([os.environ.get("FLYCTL", "flyctl"), *args], timeout=180))


def completed_snapshot(snapshots, previous_ids, requested_after):
    for item in snapshots:
        if item.get("id") in previous_ids or item.get("status") != "created":
            continue
        try:
            created = datetime.fromisoformat(item["created_at"]).timestamp()
        except (KeyError, ValueError, TypeError):
            continue
        # Fly reports creation timestamps at second precision. The new-ID check
        # rejects existing snapshots without losing a new one in this same second.
        if created >= int(requested_after) and str(item.get("id", "")).startswith(
            "vs_"
        ):
            return item["id"]
    return None


def bootstrap_safe(files, live_sha, configured_base):
    """One-time release-control bootstrap; app change is exactly health metadata."""
    if not valid_sha(configured_base) or live_sha != configured_base:
        return False
    infrastructure = {
        ".github/workflows/ci.yml",
        ".github/workflows/deploy-production.yml",
        ".github/pull_request_template.md",
        "scripts/release_control.py",
        "README.md",
        "RESEARCH_ASSISTANT_PLAN.md",
    }
    # Already-reviewed workspace exclusions on main, absent from the live image.
    # Exact blob identities prevent this exception from accepting broader edits.
    workspace_blobs = {
        ".dockerignore": "afe28af94eccc35188c06fd805064c1ea1a68c15",
        ".gitignore": "d0383ccc704945c6142e8e7a339266818053002d",
    }
    expected = {
        "Dockerfile": (
            [],
            ["LABEL org.opencontainers.image.revision=$SABER_BUILD_SHA"],
        ),
        "src/sabermetrics/ui/app.py": (
            ['return {"status": "ok", "version": version("sabermetrics")}'],
            [
                "return {",
                '"status": "ok",',
                '"version": version("sabermetrics"),',
                '"build_sha": os.environ.get("SABER_BUILD_SHA", "unknown"),',
                "}",
            ],
        ),
    }
    for item in files:
        path = item["filename"]
        if item.get("previous_filename") and not release_paths_safe(
            [path, item["previous_filename"]]
        ):
            return False
        if path in workspace_blobs:
            if item.get("sha") != workspace_blobs[path]:
                return False
            continue
        if path in infrastructure or release_paths_safe([path]):
            continue
        if (
            path not in expected
            or item.get("status") != "modified"
            or "patch" not in item
        ):
            return False
        removed, added = [], []
        for line in item["patch"].splitlines():
            if line.startswith("-"):
                removed.append(line[1:].strip())
            if line.startswith("+"):
                added.append(line[1:].strip())
        if (removed, added) != expected[path]:
            return False
    return bool(files)


def online_backup_program(destination, source="/data/sabermetrics.db", scratch="/tmp"):
    """A consistent SQLite backup, staged off-volume and verified after compression."""
    return (
        f"source={source!r}\ndestination={destination!r}\nscratch={scratch!r}\n" + """
import gzip, hashlib, json, os, pathlib, shutil, sqlite3, tempfile, uuid
source, destination = pathlib.Path(source), pathlib.Path(destination)
destination.parent.mkdir(parents=True, exist_ok=True)
reserve = 50_000_000
source_size = source.stat().st_size
if shutil.disk_usage(scratch).free < source_size * 1.2 + reserve:
    raise RuntimeError('Insufficient temporary disk space for online backup')
if destination.exists():
    raise RuntimeError('Refusing to overwrite an existing release backup')
partial = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.partial')
try:
    with tempfile.TemporaryDirectory(prefix='release-backup-', dir=scratch) as temporary:
        staged = pathlib.Path(temporary) / 'snapshot.db'
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as reader:
            with sqlite3.connect(staged) as writer:
                reader.backup(writer)
                if writer.execute('pragma integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed')
        reader.close()
        writer.close()
        expected = hashlib.sha256()
        with staged.open('rb') as reader, partial.open('xb') as raw:
            os.chmod(partial, 0o600)
            with gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=1, mtime=0) as packed:
                while chunk := reader.read(1024 * 1024):
                    if shutil.disk_usage(destination.parent).free < reserve + 2 * 1024 * 1024:
                        raise RuntimeError('Insufficient data-volume space for compressed backup')
                    expected.update(chunk)
                    packed.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())
        actual = hashlib.sha256()
        with gzip.open(partial, 'rb') as reader:
            while chunk := reader.read(1024 * 1024):
                actual.update(chunk)
        if actual.digest() != expected.digest():
            raise RuntimeError('Compressed backup verification failed')
        os.replace(partial, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        print(json.dumps({'backup': 'ok', 'sha256': actual.hexdigest(),
            'source_bytes': staged.stat().st_size, 'compressed_bytes': destination.stat().st_size}))
finally:
    partial.unlink(missing_ok=True)
"""
    )


def deploy(folder):
    require(
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY,
        "Deploy only through the main manual workflow",
    )
    require(
        bool(os.environ.get("FLY_API_TOKEN")), "Production Fly credential is missing"
    )
    initial = json.loads((folder / "manifest.json").read_text())
    run = get_run(initial["run_id"])
    manifest = validate_bundle(folder, expected_sha=run["head_sha"], run=run)
    expected_machine, expected_volume = os.environ.get(
        "EXPECTED_MACHINE_ID", ""
    ), os.environ.get("EXPECTED_VOLUME_ID", "")
    machine = validate_machine(
        fly_json("machine", "list", "-a", APP, "--json"),
        expected_machine,
        expected_volume,
    )
    live = json.loads(
        execute(
            [
                os.environ.get("FLYCTL", "flyctl"),
                "machine",
                "exec",
                machine["id"],
                "python -c "
                + shlex.quote(
                    "import os,json;print(json.dumps({'sha':os.environ.get('SABER_BUILD_SHA','unknown')}))"
                ),
                "-a",
                APP,
                "--json",
            ]
        )
    )
    require(live.get("exit_code", 0) == 0, "Could not read live release identity")
    live_sha = json.loads(live["stdout"])["sha"]
    require(valid_sha(live_sha), "Live release identity is unknown")
    comparison = gh(f"/compare/{live_sha}...{manifest['sha']}")
    require(
        comparison.get("status") == "ahead" and len(comparison.get("files", [])) < 300,
        "Release is not a bounded forward update from the live commit",
    )
    paths = [item["filename"] for item in comparison["files"]]
    paths.extend(
        item["previous_filename"]
        for item in comparison["files"]
        if item.get("previous_filename")
    )
    require(
        release_paths_safe(paths)
        or maintenance_authorized(live_sha, manifest["sha"])
        or bootstrap_safe(
            comparison["files"],
            live_sha,
            os.environ.get("RELEASE_BOOTSTRAP_BASE_SHA", ""),
        ),
        "Release includes backend/config/dependency changes; use the reviewed rollout runbook",
    )
    image_ref = machine.get("image_ref", {})
    old_digest = image_ref.get("digest", "")
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", old_digest),
        "Missing immutable rollback image",
    )
    receipt = {
        "status": "preparing",
        "sha": manifest["sha"],
        "image_id": manifest["image_id"],
        "previous_sha": live_sha,
        "previous_image": f"registry.fly.io/{APP}@{old_digest}",
        "machine": expected_machine,
        "volume": expected_volume,
        "ci_run_id": run["id"],
    }
    receipt_path = folder / "deployment.json"

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    try:
        backup = archived_backup(manifest["sha"])
        receipt["backup_path"] = backup["local_path"]
        receipt["backup_verification"] = backup
        save()
        previous_ids = {
            item.get("id")
            for item in fly_json(
                "volumes", "snapshots", "list", expected_volume, "-a", APP, "--json"
            )
        }
        requested_after = time.time()
        # Pinned flyctl accepts --json here but prints a scheduling message, not
        # a JSON snapshot. Observe a newly completed snapshot through the list API.
        execute(
            [
                os.environ.get("FLYCTL", "flyctl"),
                "volumes",
                "snapshots",
                "create",
                expected_volume,
                "-a",
                APP,
            ]
        )
        snapshot_id = None
        for _ in range(90):
            snapshots = fly_json(
                "volumes", "snapshots", "list", expected_volume, "-a", APP, "--json"
            )
            snapshot_id = completed_snapshot(snapshots, previous_ids, requested_after)
            if snapshot_id:
                break
            time.sleep(2)
        else:
            raise ReleaseError("Backup volume snapshot did not complete")
        receipt["snapshot"] = snapshot_id
        save()
        load_image(folder, manifest)
        fly = os.environ.get("FLYCTL", "flyctl")
        execute([fly, "auth", "docker"], timeout=60)
        tag = f"registry.fly.io/{APP}:ci-{manifest['sha']}-{run['run_attempt']}"
        execute(["docker", "tag", manifest["image_id"], tag])
        execute(["docker", "push", tag], timeout=300)
        image = json.loads(execute(["docker", "image", "inspect", tag]))[0]
        require(
            image["Id"] == manifest["image_id"], "Registry tag changed image identity"
        )
        digests = [
            value
            for value in image["RepoDigests"]
            if value.startswith(f"registry.fly.io/{APP}@sha256:")
        ]
        require(len(digests) == 1, "Could not identify pushed registry digest")
        receipt.update(status="deploying", image=digests[0])
        save()
        # Recheck after approval, backups and upload; never deploy a stale main.
        get_run(run["id"])
        validate_machine(
            fly_json("machine", "list", "-a", APP, "--json"),
            expected_machine,
            expected_volume,
        )
        execute(
            [
                fly,
                "deploy",
                ".",
                "-a",
                APP,
                "-c",
                str(CONFIG),
                "--image",
                digests[0],
                "--strategy",
                "rolling",
                "--ha=false",
                "--yes",
            ],
            timeout=600,
        )
        current = validate_machine(
            fly_json("machine", "list", "-a", APP, "--json"),
            expected_machine,
            expected_volume,
        )
        require(
            current.get("image_ref", {}).get("digest") == digests[0].split("@", 1)[1],
            "Deployed image digest mismatch",
        )
        with urllib.request.urlopen(
            f"https://{APP}.fly.dev/healthz", timeout=20
        ) as response:
            health = json.load(response)
        require(
            health.get("status") == "ok" and health.get("build_sha") == manifest["sha"],
            "Production health or build identity mismatch; inspect recovery receipt",
        )
        receipt["status"] = "deployed"
    except Exception:
        receipt["status"] = "failed; inspect before retry or rollback"
        raise
    finally:
        save()


def install_fly(folder):
    url = f"https://github.com/superfly/flyctl/releases/download/v{FLY_VERSION}/flyctl_{FLY_VERSION}_Linux_x86_64.tar.gz"
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read(100_000_001)
    require(
        hashlib.sha256(archive).hexdigest() == FLY_ARCHIVE_SHA,
        "Fly CLI checksum mismatch",
    )
    folder.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.getmember("flyctl")
        require(member.isfile(), "Unexpected Fly CLI archive")
        stream = tar.extractfile(member)
        require(stream is not None, "Missing Fly CLI binary")
        (folder / "flyctl").write_bytes(stream.read())
        (folder / "flyctl").chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["bundle", "prepare", "deploy", "install-fly"]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        if args.command == "bundle":
            bundle(args.output)
        elif args.command == "prepare":
            prepare(args.run_id, args.output)
        elif args.command == "deploy":
            deploy(args.output)
        else:
            install_fly(args.output)
    except (ReleaseError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(
            1,
            (str(exc) if isinstance(exc, ReleaseError) else "Release failed closed")
            + "\n",
        )


if __name__ == "__main__":
    main()
