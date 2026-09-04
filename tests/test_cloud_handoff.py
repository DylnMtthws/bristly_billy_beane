"""W9 charter and deployment hand-off stay complete and non-contradictory."""

from pathlib import Path
from plistlib import loads as load_plist

ROOT = Path(__file__).resolve().parents[1]


def test_adr_028_supersedes_the_old_production_shape():
    charter = (ROOT / "CLAUDE.md").read_text()
    assert "| ADR-028 |" in charter
    assert "supersedes ADR-008 and ADR-026 for production" in charter
    assert "Binding 0.0.0.0 is allowed only inside that container" in charter
    assert "ALL modes are admin-provisioned" in charter


def test_handoff_contains_env_secrets_backup_schema_and_bootstrap_commands():
    handoff = (ROOT / "docs/deployment.md").read_text()
    for name in (
        "SABER_BIND_HOST",
        "SABER_PORT",
        "SABER_TRUSTED_PROXY",
        "SABER_DB_PATH",
        "CEDH_SIMULATOR_URL",
        "CEDH_SIMULATOR_TIMEOUT",
        "SABER_SECRET_KEY",
        "MTG_V1_DSN",
        "HF_TOKEN",
    ):
        assert f"`{name}`" in handoff
    assert "sabermetrics db-backup" in handoff
    assert "cedh-simulation-result.v2" in handoff
    assert "sabermetrics create-admin" in handoff
    assert "sabermetrics invite-user" in handoff
    assert "--base-url https://decklab.example.com" in handoff


def test_launchd_and_refresh_jobs_are_marked_legacy_only():
    plists = sorted((ROOT / "launchd").glob("*.plist"))
    assert len(plists) == 4
    for path in plists:
        text = path.read_text()
        assert "Legacy casual-generator ingestion only" in text
        assert load_plist(path.read_bytes())["Label"].startswith("com.sabermetrics.")

    refresh_scripts = (
        "nightly_refresh.py",
        "weekly_refresh.py",
        "monthly_rulings_refresh.py",
        "quarterly_set_refresh.py",
    )
    for filename in refresh_scripts:
        assert "legacy path only" in (ROOT / "scripts" / filename).read_text()[:300]


def test_optional_simulator_list_check_has_no_machine_specific_path():
    test_source = (ROOT / "tests/test_cedh_lab.py").read_text()
    assert "CEDH_KINNAN_DECK_TOML" in test_source
    assert "/Users/dylan/" not in test_source


def test_cloud_plan_definition_of_done_is_checked_off():
    plan = (ROOT / "CLOUD_ALIGNMENT_PLAN.md").read_text()
    assert "- [ ]" not in plan
    assert plan.count("- [x]") == 9
