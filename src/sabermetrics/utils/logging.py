"""Structured logging infrastructure (D8.7).

JSON structured logs with per-job log files and rotation.
Config: 10MB per file, 5 backups.
"""

import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Unknown",
                "message": str(record.exc_info[1]),
            }

        # Include extra fields if present
        for key in ("job_name", "source", "items_processed", "duration_seconds", "cost_usd"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry)


def setup_job_logging(
    job_name: str,
    log_dir: Path | None = None,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    also_stdout: bool = True,
) -> logging.Logger:
    """Configure logging for a scheduled job.

    Creates a rotating file handler writing JSON-structured logs
    to data/logs/<job_name>.log.

    Args:
        job_name: Name of the job (used for log filename).
        log_dir: Directory for log files. Defaults to data/logs/.
        level: Logging level.
        max_bytes: Max size per log file before rotation.
        backup_count: Number of rotated backups to keep.
        also_stdout: Also log to stdout (for manual runs).

    Returns:
        Configured logger for the job.
    """
    if log_dir is None:
        log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / f"{job_name}.log"

    # Get root logger and configure
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers to avoid duplicates on re-init
    root_logger.handlers.clear()

    # Rotating file handler with JSON format
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(file_handler)

    # Optional stdout handler
    if also_stdout:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(level)
        stdout_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root_logger.addHandler(stdout_handler)

    logger = logging.getLogger(f"sabermetrics.jobs.{job_name}")
    logger.info("Logging initialized for job: %s", job_name)
    logger.info("Log file: %s", log_path)

    return logger
