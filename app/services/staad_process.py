"""
Windows-safe discovery and termination of STAAD-related processes.

Prefer ``psutil`` when installed; fall back to ``tasklist`` / ``taskkill`` for names only.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Set

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StaadProcessInfo:
    """Minimal process descriptor for logging and selective termination."""

    pid: int
    name: str
    ppid: Optional[int] = None


def _names_normalized(names: Sequence[str]) -> Set[str]:
    return {n.strip().lower() for n in names if n.strip()}


def _basename_lower(exe_name: str) -> str:
    return exe_name.replace("/", "\\").split("\\")[-1].lower()


def find_staad_processes(process_names: Sequence[str]) -> List[StaadProcessInfo]:
    """
    Return running processes whose executable name matches any of ``process_names``.

    Uses ``psutil`` if available; otherwise parses ``tasklist`` output (Windows).
    """
    names = _names_normalized(process_names)
    if not names:
        return []

    try:
        import psutil  # type: ignore[import-untyped]

        out: List[StaadProcessInfo] = []
        for proc in psutil.process_iter(["pid", "name", "ppid"]):
            try:
                info = proc.info
                raw_name = str(info.get("name") or "")
                base = _basename_lower(raw_name)
                if base not in names:
                    continue
                pid = int(info["pid"])
                ppid = info.get("ppid")
                out.append(
                    StaadProcessInfo(
                        pid=pid,
                        name=raw_name,
                        ppid=int(ppid) if ppid is not None else None,
                    )
                )
            except (psutil.Error, ValueError, TypeError):
                continue
        return out
    except ImportError:
        pass

    if sys.platform != "win32":
        logger.debug("find_staad_processes: non-Windows without psutil; skipping")
        return []

    try:
        r = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("find_staad_processes: tasklist failed: %s", e)
        return []

    found: List[StaadProcessInfo] = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith('"Image Name"'):
            continue
        # "name.exe","123","Session Name","Session#","Mem Usage"
        parts = line.split('","')
        if len(parts) < 2:
            continue
        try:
            raw_name = parts[0].strip('"')
            pid_str = parts[1].strip('"')
            pid = int(pid_str)
        except (ValueError, IndexError):
            continue
        base = _basename_lower(raw_name)
        if base in names:
            found.append(StaadProcessInfo(pid=pid, name=raw_name, ppid=None))
    return found


def find_staad_children_of(parent_pids: Set[int], process_names: Sequence[str]) -> List[StaadProcessInfo]:
    """
    Restrict matches to processes whose parent PID is in ``parent_pids`` (best-effort with psutil).
    If psutil is unavailable, returns an empty list (caller should not broad-kill).
    """
    if not parent_pids:
        return []
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return []

    names = _names_normalized(process_names)
    out: List[StaadProcessInfo] = []
    for proc in psutil.process_iter(["pid", "name", "ppid"]):
        try:
            info = proc.info
            ppid = info.get("ppid")
            if ppid is None or int(ppid) not in parent_pids:
                continue
            raw_name = str(info.get("name") or "")
            base = _basename_lower(raw_name)
            if base not in names:
                continue
            out.append(
                StaadProcessInfo(
                    pid=int(info["pid"]),
                    name=str(info.get("name") or ""),
                    ppid=int(ppid),
                )
            )
        except (psutil.Error, ValueError, TypeError):
            continue
    return out


def kill_process_tree_windows(pid: int) -> bool:
    """
    Terminate a process and its descendants on Windows using ``taskkill /T /F``.

    Returns:
        True if ``taskkill`` reported success (exit code 0).
    """
    if sys.platform != "win32":
        try:
            import psutil  # type: ignore[import-untyped]

            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            parent.kill()
            return True
        except Exception:
            logger.exception("kill_process_tree_windows: fallback failed for pid=%s", pid)
            return False

    try:
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        ok = r.returncode == 0
        if ok:
            logger.info("taskkill /T /F succeeded for pid=%s", pid)
        else:
            logger.warning(
                "taskkill pid=%s exit=%s out=%s err=%s",
                pid,
                r.returncode,
                (r.stdout or "")[:500],
                (r.stderr or "")[:500],
            )
        return ok
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("kill_process_tree_windows: taskkill failed for pid=%s: %s", pid, e)
        return False


def kill_staad_processes(
    processes: Sequence[StaadProcessInfo],
    *,
    reason: str = "",
) -> int:
    """
    Terminate each process in the list via ``kill_process_tree_windows``.

    Returns:
        Number of processes for which termination was attempted.
    """
    n = 0
    for p in processes:
        logger.warning(
            "Terminating STAAD-related process pid=%s name=%s reason=%s",
            p.pid,
            p.name,
            reason or "cleanup",
        )
        kill_process_tree_windows(p.pid)
        n += 1
    return n


def kill_staad_processes_by_name(
    process_names: Sequence[str],
    *,
    reason: str = "",
) -> int:
    """
    Terminate STAAD-related processes by executable image name.

    On Windows this mirrors ``taskkill /F /IM <name>`` for each configured image and
    falls back to enumerating processes when needed.
    """
    names = [name.strip() for name in process_names if name.strip()]
    if not names:
        return 0

    if sys.platform == "win32":
        killed = 0
        for name in names:
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", name],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                stdout = (result.stdout or "").strip()
                stderr = (result.stderr or "").strip()
                if result.returncode == 0:
                    killed += 1
                    logger.warning(
                        "taskkill /F /IM %s succeeded reason=%s out=%s",
                        name,
                        reason or "cleanup",
                        stdout[:300],
                    )
                    continue
                no_instance = "no running instance" in stderr.lower() or "not found" in stderr.lower()
                if no_instance:
                    continue
                logger.warning(
                    "taskkill /F /IM %s exit=%s reason=%s out=%s err=%s",
                    name,
                    result.returncode,
                    reason or "cleanup",
                    stdout[:300],
                    stderr[:300],
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning("taskkill /F /IM %s failed reason=%s error=%s", name, reason or "cleanup", exc)
        return killed

    return kill_staad_processes(find_staad_processes(names), reason=reason)


def cleanup_staad_processes_referencing_job_dir(
    job_dir: Path,
    process_names: Sequence[str],
) -> int:
    """
    Terminate STAAD-related processes whose command line contains the job directory path.

    This targets orphaned analysis processes that survived after the extractor exited,
    without killing unrelated STAAD sessions on the server.
    """
    job_dir = Path(job_dir).resolve()
    job_tag = str(job_dir)
    names = _names_normalized(process_names)
    if not names:
        return 0

    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return 0

    killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            raw_name = str(info.get("name") or "")
            base = _basename_lower(raw_name)
            if base not in names:
                continue
            cmdline = info.get("cmdline") or []
            flat = " ".join(cmdline) if cmdline else ""
            if job_tag.lower() not in flat.lower():
                continue
            pid = int(info["pid"])
            logger.warning(
                "Killing STAAD process referencing job dir: pid=%s name=%s",
                pid,
                raw_name,
            )
            kill_process_tree_windows(pid)
            killed += 1
        except Exception:
            continue
    return killed
