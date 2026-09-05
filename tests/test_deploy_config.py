"""The W8 platform draft remains a single-machine, volume-backed plan."""

from pathlib import Path
from tomllib import loads

ROOT = Path(__file__).resolve().parents[1]


def test_fly_draft_matches_the_pinned_runtime_contract():
    text = (ROOT / "deploy/fly.toml").read_text()
    config = loads(text)

    assert config["build"]["dockerfile"] == "Dockerfile"
    assert config["env"] == {
        "SABER_PUBLIC": "1",
        "SABER_AUTH_MODE": "hybrid",
        "SABER_BIND_HOST": "0.0.0.0",
        "SABER_PORT": "8080",
        "SABER_TRUSTED_PROXY": "*",
        "SABER_DB_PATH": "/data/sabermetrics.db",
    }
    assert config["http_service"]["internal_port"] == 8080
    assert config["http_service"]["auto_stop_machines"] == "off"
    assert config["http_service"]["min_machines_running"] == 1
    assert config["http_service"]["checks"] == [{"path": "/healthz", "interval": "30s"}]
    assert config["mounts"] == {"source": "decklab_data", "destination": "/data"}
    assert len(config["vm"]) == 1


def test_fly_draft_names_secrets_without_containing_values():
    text = (ROOT / "deploy/fly.toml").read_text()
    header = "\n".join(text.splitlines()[:3])
    for name in (
        "SABER_SECRET_KEY",
        "MTG_V1_DSN",
        "HF_TOKEN",
        "CEDH_SIMULATOR_URL",
    ):
        assert name in header
        assert name not in loads(text).get("env", {})
