"""Static guarantees for the W6 production image and CI smoke test."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_has_the_frozen_runtime_contract():
    dockerfile = (ROOT / "Dockerfile").read_text()
    for required in (
        "FROM python:3.11-slim-bookworm",
        'org.opencontainers.image.version="0.1.0"',
        "SABER_BIND_HOST=0.0.0.0",
        "SABER_PORT=8080",
        "SABER_DB_PATH=/data/sabermetrics.db",
        'pip install --no-cache-dir ".[postgres]"',
        "USER 1001:1001",
        "EXPOSE 8080",
        'CMD ["sabermetrics", "serve"]',
    ):
        assert required in dockerfile


def test_dockerignore_excludes_local_state_and_secrets():
    ignored = set((ROOT / ".dockerignore").read_text().splitlines())
    assert {".venv", "data/", ".env*", "tests/", "build/", "*.db"} <= ignored


def test_ci_builds_without_push_and_smokes_both_routes():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "docker build --platform linux/amd64" in workflow
    assert "docker push" not in workflow
    assert 'test "$size_bytes" -lt 400000000' in workflow
    assert "/healthz" in workflow
    assert "/login" in workflow
    assert "--tmpfs /data:rw,uid=1001,gid=1001" in workflow
