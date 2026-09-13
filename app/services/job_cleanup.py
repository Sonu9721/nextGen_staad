"""
Age-based cleanup of job folders under ``D:\\STAADJobs`` (or configured base).
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


class CleanupStats(NamedTuple):
    deleted: int
    skipped: int
    errors: list[str]


def cleanup_old_job_folders(base_dir: Path, max_age_hours: float, prefix: str = "job_") -> CleanupStats:
    """
    Remove subdirectories of ``base_dir`` whose names start with ``prefix`` and whose
    mtime is older than ``max_age_hours``.

    Args:
        base_dir: Root directory containing ``job_<uuid>`` folders.
        max_age_hours: Folders with mtime older than this are deleted.
        prefix: Only consider directories whose name starts with this string.

    Returns:
        Counts of deleted and skipped folders plus non-fatal error messages.
    """
    base_dir = Path(base_dir)
    if not base_dir.is_dir():
        logger.warning("cleanup_old_job_folders: base_dir does not exist: %s", base_dir)
        return CleanupStats(0, 0, [f"base_dir missing: {base_dir}"])

    cutoff = time.time() - max_age_hours * 3600.0
    deleted = 0
    skipped = 0
    errors: list[str] = []

    try:
        entries = list(base_dir.iterdir())
    except OSError as e:
        logger.exception("cleanup_old_job_folders: cannot list %s: %s", base_dir, e)
        return CleanupStats(0, 0, [str(e)])

    for entry in entries:
        if not entry.is_dir():
            continue
        if not entry.name.startswith(prefix):
            skipped += 1
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError as e:
            errors.append(f"{entry.name}: stat failed: {e}")
            continue
        if mtime >= cutoff:
            skipped += 1
            continue
        try:
            shutil.rmtree(entry, ignore_errors=False)
            deleted += 1
            logger.info("Removed stale job folder: %s (mtime age > %.1f h)", entry.name, max_age_hours)
        except OSError as e:
            msg = f"{entry.name}: rmtree failed: {e}"
            errors.append(msg)
            logger.warning("%s", msg)

    logger.info(
        "cleanup_old_job_folders: deleted=%d skipped=%d errors=%d base=%s",
        deleted,
        skipped,
        len(errors),
        base_dir,
    )
    return CleanupStats(deleted, skipped, errors)
