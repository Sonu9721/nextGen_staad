"""
Windows EC2 bootstrap: redirect temp/scratch to fixed C: paths before any subprocess or STAAD use.

Call :func:`configure_runtime_environment` once at process startup (before creating
temporary paths or spawning workers).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_TEMP = Path(r"C:\Temp")
_DEFAULT_JOBS = Path(r"C:\STAADJobs")
_DEFAULT_LOGS = Path(r"C:\Logs")


def _default_triplet() -> tuple[Path, Path, Path]:
    """Use fixed C: defaults unless the operator overrides them via environment."""
    if os.environ.get("ANALYSIS_BACKEND", "inhouse").lower() == "inhouse":
        runtime = Path(__file__).resolve().parents[2] / "runtime"
        return runtime / "temp", runtime / "jobs", runtime / "logs"
    if sys.platform == "win32":
        return _DEFAULT_TEMP, _DEFAULT_JOBS, _DEFAULT_LOGS
    return Path("/tmp"), Path("/tmp/STAADJobs"), Path("/tmp/Logs")


def _env_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    if raw:
        return Path(raw)
    return default


def ensure_directory(path: Path) -> None:
    """Create ``path`` if missing (including parents)."""
    path.mkdir(parents=True, exist_ok=True)


def configure_runtime_environment() -> tuple[Path, Path, Path]:
    """
    Set process-wide temp directories to the configured C: locations, create required
    folders, and assign ``tempfile.tempdir``.

    Must run before any code uses :func:`tempfile.mkdtemp`, :class:`tempfile.TemporaryDirectory`,
    or STAAD-related subprocesses.

    Returns:
        Tuple of ``(temp_dir, staad_jobs_dir, logs_dir)`` as resolved paths.
    """
    dt, dj, dl = _default_triplet()
    temp_dir = _env_path("TEMP_DIR", dt)
    staad_jobs_dir = _env_path("STAAD_JOBS_DIR", dj)
    logs_dir = _env_path("LOGS_DIR", dl)

    for p in (temp_dir, staad_jobs_dir, logs_dir):
        ensure_directory(p)

    temp_str = str(temp_dir.resolve())
    os.environ["TEMP"] = temp_str
    os.environ["TMP"] = temp_str
    os.environ["TEMP_DIR"] = temp_str
    os.environ["STAAD_JOBS_DIR"] = str(staad_jobs_dir.resolve())
    os.environ["LOGS_DIR"] = str(logs_dir.resolve())
    tempfile.tempdir = temp_str

    logger.info(
        "Runtime environment configured: TEMP=%s TMP=%s tempfile.tempdir=%s STAAD_JOBS=%s LOGS=%s",
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        tempfile.gettempdir(),
        os.environ.get("STAAD_JOBS_DIR"),
        os.environ.get("LOGS_DIR"),
    )
    return temp_dir.resolve(), staad_jobs_dir.resolve(), logs_dir.resolve()
