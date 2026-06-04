"""Tests for Phase 8 Refresh Automation (D8.1-D8.7).

Tests logging infrastructure, script imports, launchd plists,
and installer script.
"""

import json
import logging
import os
import tempfile
from pathlib import Path


from sabermetrics.utils.logging import JSONFormatter, setup_job_logging

DB_PATH = Path("data/sabermetrics.db")
HAS_DB = DB_PATH.exists()
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --- Logging infrastructure tests (D8.7) ---


def test_json_formatter() -> None:
    """JSONFormatter produces valid JSON."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Test message %s",
        args=("hello",),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "Test message hello"
    assert "timestamp" in parsed


def test_json_formatter_with_exception() -> None:
    """JSONFormatter includes exception info."""
    formatter = JSONFormatter()
    try:
        raise ValueError("test error")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="test.py",
        lineno=1,
        msg="Failed",
        args=(),
        exc_info=exc_info,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["exception"]["type"] == "ValueError"
    assert "test error" in parsed["exception"]["message"]


def test_setup_job_logging() -> None:
    """setup_job_logging creates log file and returns logger."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        logger = setup_job_logging(
            "test_job", log_dir=log_dir, also_stdout=False
        )
        logger.info("Test log entry")

        log_file = log_dir / "test_job.log"
        assert log_file.exists()

        content = log_file.read_text()
        assert "Test log entry" in content

        # Verify it's valid JSON
        for line in content.strip().split("\n"):
            if line:
                parsed = json.loads(line)
                assert "timestamp" in parsed

    # Cleanup: reset root logger
    logging.getLogger().handlers.clear()


def test_log_rotation_config() -> None:
    """Log rotation is configured with correct defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        setup_job_logging("rotation_test", log_dir=log_dir, also_stdout=False)

        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers
            if hasattr(h, "maxBytes")
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == 10 * 1024 * 1024  # 10MB
        assert file_handlers[0].backupCount == 5

    logging.getLogger().handlers.clear()


# --- Script existence tests ---


def test_nightly_script_exists() -> None:
    """Nightly refresh script exists."""
    assert (PROJECT_ROOT / "scripts" / "nightly_refresh.py").exists()


def test_weekly_script_exists() -> None:
    """Weekly refresh script exists."""
    assert (PROJECT_ROOT / "scripts" / "weekly_refresh.py").exists()


def test_monthly_script_exists() -> None:
    """Monthly rulings refresh script exists."""
    assert (PROJECT_ROOT / "scripts" / "monthly_rulings_refresh.py").exists()


def test_quarterly_script_exists() -> None:
    """Quarterly set refresh script exists."""
    assert (PROJECT_ROOT / "scripts" / "quarterly_set_refresh.py").exists()


# --- Script import tests ---


def test_nightly_script_importable() -> None:
    """Nightly script can be imported without error."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "nightly_refresh",
        PROJECT_ROOT / "scripts" / "nightly_refresh.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")


def test_weekly_script_importable() -> None:
    """Weekly script can be imported without error."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "weekly_refresh",
        PROJECT_ROOT / "scripts" / "weekly_refresh.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")


def test_monthly_script_importable() -> None:
    """Monthly script can be imported without error."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "monthly_rulings_refresh",
        PROJECT_ROOT / "scripts" / "monthly_rulings_refresh.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")


def test_quarterly_script_importable() -> None:
    """Quarterly script can be imported without error."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "quarterly_set_refresh",
        PROJECT_ROOT / "scripts" / "quarterly_set_refresh.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")


# --- launchd plist tests (D8.5) ---


def test_launchd_plists_exist() -> None:
    """All 4 launchd plist templates exist."""
    plist_dir = PROJECT_ROOT / "launchd"
    expected = [
        "com.sabermetrics.nightly.plist",
        "com.sabermetrics.weekly.plist",
        "com.sabermetrics.monthly.plist",
        "com.sabermetrics.quarterly.plist",
    ]
    for name in expected:
        assert (plist_dir / name).exists(), f"Missing plist: {name}"


def test_launchd_plists_have_placeholders() -> None:
    """Plists contain placeholder tokens for install script."""
    plist_dir = PROJECT_ROOT / "launchd"
    for plist in plist_dir.glob("*.plist"):
        content = plist.read_text()
        assert "__PROJECT_DIR__" in content, f"{plist.name} missing __PROJECT_DIR__"
        assert "__VENV_PYTHON__" in content, f"{plist.name} missing __VENV_PYTHON__"


def test_nightly_plist_schedule() -> None:
    """Nightly plist schedules at 2am."""
    content = (PROJECT_ROOT / "launchd" / "com.sabermetrics.nightly.plist").read_text()
    assert "<key>Hour</key>" in content
    assert "<integer>2</integer>" in content


def test_weekly_plist_schedule() -> None:
    """Weekly plist schedules Sunday 3am."""
    content = (PROJECT_ROOT / "launchd" / "com.sabermetrics.weekly.plist").read_text()
    assert "<key>Weekday</key>" in content
    assert "<integer>0</integer>" in content  # Sunday
    assert "<integer>3</integer>" in content  # 3am


# --- Installer script test (D8.6) ---


def test_installer_script_exists() -> None:
    """Install script exists and is executable."""
    script = PROJECT_ROOT / "scripts" / "install_launchd.sh"
    assert script.exists()
    assert os.access(script, os.X_OK)


def test_installer_script_content() -> None:
    """Install script handles all plists and substitutes placeholders."""
    content = (PROJECT_ROOT / "scripts" / "install_launchd.sh").read_text()
    assert "com.sabermetrics.nightly.plist" in content
    assert "com.sabermetrics.weekly.plist" in content
    assert "com.sabermetrics.monthly.plist" in content
    assert "com.sabermetrics.quarterly.plist" in content
    assert "__PROJECT_DIR__" in content
    assert "__VENV_PYTHON__" in content
    assert "launchctl load" in content


# --- CLI refresh-set command test ---


def test_refresh_set_command_registered() -> None:
    """refresh-set command is registered in CLI."""
    from sabermetrics.main import cli
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli, ["refresh-set", "--help"])
    assert result.exit_code == 0
    assert "set_code" in result.output.lower() or "SET_CODE" in result.output
