"""
Managed STAAD extraction worker around the bundled ``staad-max-extractor`` module.

This replaces the legacy CLI/listing parser workflow with a bounded child process that
imports and executes the new OpenSTAAD-based extractor directly.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import logging
import multiprocessing
import random
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.config import Settings
from app.schemas import ApiError
from app.services.staad_process import (
    cleanup_staad_processes_referencing_job_dir,
    kill_process_tree_windows,
    kill_staad_processes_by_name,
)

logger = logging.getLogger(__name__)
_NON_WINDOWS_STAAD_LOCK = threading.Lock()


class ExtractorExecutionError(Exception):
    """Raised when the extractor worker fails, times out, or returns invalid output."""

    def __init__(
        self,
        message: str,
        retryable: bool,
        stderr_excerpt: Optional[str] = None,
        error_code: str = "extractor_failed",
        attempts: int = 1,
        stage: Optional[str] = None,
        hint: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.stderr_excerpt = stderr_excerpt
        self.error_code = error_code
        self.attempts = attempts
        self.stage = stage
        self.hint = hint
        self.details = details or {}


def _safe_excerpt(text: str, limit: int) -> str:
    return text[:limit] if text else ""


def _is_transient_from_message(message: str) -> bool:
    low = message.lower()
    transient_tokens = (
        "timeout",
        "temporar",
        "busy",
        "locked",
        "access is denied",
        "timed out waiting",
        "staad.pro does not appear to be running",
    )
    return any(token in low for token in transient_tokens)


def _infer_failure_stage(message: str) -> str | None:
    lower_message = message.lower()
    if "staad file not found" in lower_message or "expected a .std file" in lower_message:
        return "input_validation"
    if "load case" in lower_message:
        return "load_case_selection"
    if "timed out waiting" in lower_message or "analysis failed" in lower_message or "results are available" in lower_message:
        return "analysis"
    if (
        "openstaad" in lower_message
        or "staad.pro" in lower_message
        or "interactive desktop session" in lower_message
        or "non-interactive windows session" in lower_message
    ):
        return "connection"
    if any(token in lower_message for token in ("bending moment", "displacement", "axial force", "shear force")):
        return "result_extraction"
    return "execution"


def _build_failure_hint(message: str, error_code: str) -> str | None:
    lower_message = message.lower()
    if error_code == "missing_dependency":
        return "Install the Python dependencies on the Windows host, especially pywin32."
    if error_code == "invalid_input_file":
        return "Upload a valid .std file and verify the path and extension before submitting the job."
    if error_code == "invalid_load_case":
        return "Check that the requested load case exists in the model, or use loadcase=all."
    if error_code == "timeout":
        return "Increase timeout_seconds or open the model once manually in STAAD to clear any blocking dialogs."
    if error_code == "non_interactive_session":
        return "Run the API and STAAD under the logged-in Windows desktop session. In Task Scheduler, use 'Run only when user is logged on'."
    if error_code == "staad_connection_failed":
        if "interactive desktop session" in lower_message or "license" in lower_message or "sign-in" in lower_message:
            return "Sign in to Bentley inside the active Windows desktop session and dismiss any STAAD modal dialogs."
        return "Verify STAAD.Pro is installed, licensed, and able to expose OpenSTAAD in the active Windows session."
    if error_code == "license_not_available":
        return "Open STAAD manually on the server once, complete Bentley sign-in/licensing, and keep it in an interactive session."
    return None


def _classify_failure(message: str, settings: Settings) -> ApiError:
    lower_message = message.lower()
    deterministic_tokens = (
        "staad file not found",
        "expected a .std file",
        "invalid load case",
        "no load cases could be detected",
        "pywin32 is not available",
        "confirm staad.pro is installed",
        "openstaad is registered",
        "geometry/output interfaces",
        "interactive desktop session",
        "non-interactive windows session",
        "run only when user is logged on",
        "task scheduler",
        "license/sign-in prompt",
        "modal staad dialog",
    )
    retryable = _is_transient_from_message(message)
    if any(token in lower_message for token in deterministic_tokens):
        retryable = False

    code = "extractor_failed"
    if "timeout" in lower_message:
        code = "timeout"
    elif "load case" in lower_message:
        code = "invalid_load_case"
    elif "staad file not found" in lower_message or "expected a .std file" in lower_message:
        code = "invalid_input_file"
    elif "pywin32 is not available" in lower_message:
        code = "missing_dependency"
    elif "non-interactive windows session" in lower_message or "run only when user is logged on" in lower_message:
        code = "non_interactive_session"
    elif "interactive desktop session" in lower_message or "geometry/output interfaces" in lower_message:
        code = "staad_connection_failed"
    elif "openstaad" in lower_message or "staad.pro" in lower_message:
        code = "staad_connection_failed"

    return ApiError(
        error_code=code,
        message=message,
        retryable=retryable,
        stage=_infer_failure_stage(message),
        hint=_build_failure_hint(message, code),
        last_stderr_excerpt=_safe_excerpt(message, settings.max_stderr_excerpt_chars),
    )


def compute_outer_timeout_seconds(settings: Settings, *, per_attempt_staad_timeout: int) -> float:
    """
    Total wall-clock budget for one extraction attempt.

    The managed worker gets a cushion beyond the STAAD analysis timeout for startup,
    COM attach, and result serialization.
    """
    return (
        float(per_attempt_staad_timeout)
        + float(settings.staad_startup_timeout_seconds)
        + float(settings.staad_attach_timeout_seconds)
        + float(settings.staad_launch_wait_seconds)
        + float(settings.extractor_timeout_cushion_seconds)
    )


@contextlib.contextmanager
def _acquire_staad_runtime_lock(settings: Settings, *, job_id: str):
    """
    Acquire a best-effort cross-process STAAD lock so only one API worker can
    automate STAAD at a time on the host.
    """
    if settings.allow_parallel_staad:
        yield
        return

    deadline = time.monotonic() + max(1, int(settings.staad_lock_timeout_seconds))
    lock_path = settings.temp_dir / "staad-runtime.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform != "win32":
        with _NON_WINDOWS_STAAD_LOCK:
            yield
        return

    import msvcrt

    handle = lock_path.open("a+b")
    acquired = False
    try:
        while time.monotonic() < deadline:
            try:
                handle.seek(0)
                handle.write(b"1")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                logger.info("job=%s acquired_staad_lock path=%s", job_id, lock_path)
                break
            except OSError:
                time.sleep(1.0)

        if not acquired:
            raise ExtractorExecutionError(
                message="Timed out waiting for the single STAAD runtime lock.",
                retryable=True,
                error_code="staad_lock_timeout",
                attempts=1,
                stage="queue",
                hint="Ensure only one API worker/process is running STAAD on this host.",
            )

        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                logger.info("job=%s released_staad_lock path=%s", job_id, lock_path)
            except OSError:
                logger.warning("job=%s failed to release STAAD lock cleanly", job_id)
        handle.close()


def cleanup_job_artifacts(job_dir: Path, *, std_path: Path, keep_anl: bool) -> None:
    """
    Delete generated files in ``job_dir``, keeping the uploaded ``.std`` and optionally ``.anl``.
    """
    job_dir = Path(job_dir)
    std_name = std_path.name
    stem = std_path.stem
    keep_names = {std_name.lower()}
    if keep_anl:
        keep_names.add(f"{stem}.anl".lower())

    for path in list(job_dir.iterdir()):
        try:
            if not path.is_file():
                continue
            if path.name.lower() in keep_names:
                continue
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("cleanup_job_artifacts: could not remove %s: %s", path, exc)


def cleanup_job_artifacts_with_retries(
    job_dir: Path,
    *,
    std_path: Path,
    keep_anl: bool,
    retry_seconds: float,
    retry_interval_seconds: float,
) -> None:
    """
    Delete generated files with a short retry window for STAAD/Windows file locks.
    """
    job_dir = Path(job_dir)
    std_name = std_path.name
    stem = std_path.stem
    keep_names = {std_name.lower()}
    if keep_anl:
        keep_names.add(f"{stem}.anl".lower())

    retry_seconds = max(0.0, float(retry_seconds))
    retry_interval_seconds = max(0.1, float(retry_interval_seconds))

    for path in list(job_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.lower() in keep_names:
            continue

        deadline = time.monotonic() + retry_seconds
        while True:
            try:
                path.unlink(missing_ok=True)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                winerror = getattr(exc, "winerror", None)
                is_lock_error = winerror in (32, 33) or exc.errno in (13,)
                if not is_lock_error or time.monotonic() >= deadline:
                    logger.warning("cleanup_job_artifacts: could not remove %s: %s", path, exc)
                    break
                time.sleep(retry_interval_seconds)


def _load_extractor_module(repo_root: Path):
    module_path = repo_root / "staad-max-extractor" / "extractor.py"
    if not module_path.is_file():
        raise FileNotFoundError(f"Extractor module not found: {module_path}")

    spec = importlib.util.spec_from_file_location("staad_max_extractor_runtime", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load extractor module spec from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_extraction_child(
    queue: multiprocessing.queues.Queue,
    *,
    repo_root: str,
    std_file_path: str,
    timeout_seconds: int,
    include_combinations: bool,
    poll_interval_seconds: float,
    launch_wait_seconds: float,
    startup_timeout_seconds: int,
    attach_timeout_seconds: int,
    extraction_flow: str = "standard",
    generation_request_path: str | None = None,
) -> None:
    try:
        module = _load_extractor_module(Path(repo_root))

        def progress_callback(progress_percent: int, message: str) -> None:
            queue.put(
                {
                    "progress": {
                        "percent": int(progress_percent),
                        "message": message,
                    }
                }
            )

        config = module.ExtractionConfig(
            file_path=Path(std_file_path),
            load_case="all",
            moment_axis="MZ",
            moment_mode="internal-envelope",
            include_combinations=include_combinations,
            displacement_axis="RESULTANT",
            source_force_unit=None,
            source_length_unit=None,
            moment_output_unit="kN-m",
            displacement_output_unit="mm",
            analysis_timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            launch_wait_seconds=launch_wait_seconds,
            startup_timeout_seconds=startup_timeout_seconds,
            attach_timeout_seconds=attach_timeout_seconds,
            output_path=None,
            progress_callback=progress_callback,
            extraction_flow=extraction_flow,
            generation_request_path=Path(generation_request_path) if generation_request_path else None,
        )
        result = module.run(config)
        queue.put({"ok": True, "result": result})
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "traceback": traceback.format_exc(),
            }
        )


def _run_preflight_child(queue: multiprocessing.queues.Queue, *, repo_root: str, timeout_seconds: int) -> None:
    try:
        module = _load_extractor_module(Path(repo_root))
        ensure_interactive = getattr(module, "_ensure_interactive_windows_session", None)
        if callable(ensure_interactive):
            ensure_interactive()
        executable = module.find_staad_executable()
        if executable is None:
            raise RuntimeError(
                "Could not find Bentley.Staad.exe or SProStaad.exe under the STAAD.Pro 2025 installation."
            )

        session = module._connect_via_openstaadpy(None)
        if not module._session_is_attach_ready(session):
            session = module.wait_for_com_connection(timeout_seconds=max(5, timeout_seconds // 3))
        if not module._session_is_attach_ready(session):
            module.launch_staad_application(None)
            session = module.wait_for_openstaadpy_connection(None, timeout_seconds=timeout_seconds)
        if not module._session_is_attach_ready(session):
            session = module.wait_for_com_connection(timeout_seconds=timeout_seconds)
        if not module._session_is_attach_ready(session):
            raise RuntimeError(
                "STAAD.Pro launched, but OpenSTAAD did not expose usable Geometry/Output interfaces. "
                "Licensing/sign-in or another STAAD dialog may be blocking the interactive desktop session."
            )

        current_file = None
        try:
            current_file = module._safe_call(lambda: session.root.GetSTAADFile())
        except Exception:
            current_file = None

        queue.put(
            {
                "ok": True,
                "staad_executable": str(executable),
                "current_file": str(current_file) if current_file else None,
            }
        )
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }
        )


def run_staad_preflight(*, settings: Settings, force: bool = False) -> dict[str, Any]:
    """
    Verify that STAAD can be launched/attached before a job is accepted.

    This is intended to fail fast when licensing/sign-in dialogs block automation.
    """
    if not settings.enable_staad_preflight and not force:
        return {"enabled": False, "status": "skipped"}

    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue(maxsize=1)
    repo_root = Path(__file__).resolve().parent.parent.parent
    timeout_seconds = max(5, int(settings.staad_preflight_timeout_seconds))
    outer_timeout = float(timeout_seconds) + 10.0

    proc = ctx.Process(
        target=_run_preflight_child,
        kwargs={
            "queue": result_queue,
            "repo_root": str(repo_root),
            "timeout_seconds": timeout_seconds,
        },
        daemon=False,
    )
    proc.start()

    try:
        proc.join(timeout=outer_timeout)
        if proc.is_alive():
            if proc.pid is not None:
                kill_process_tree_windows(proc.pid)
            proc.join(timeout=10)
            raise ExtractorExecutionError(
                message=(
                    "STAAD preflight timed out while launching or attaching to STAAD.Pro. "
                    "Licensing/sign-in may be blocking the desktop session."
                ),
                retryable=True,
                error_code="license_not_available",
                attempts=1,
                stage="preflight",
                hint=_build_failure_hint("Licensing/sign-in may be blocking the desktop session.", "license_not_available"),
            )

        try:
            payload = result_queue.get_nowait()
        except Exception as exc:
            raise ExtractorExecutionError(
                message=f"STAAD preflight exited without returning data (exit={proc.exitcode}).",
                retryable=True,
                error_code="staad_preflight_failed",
                stderr_excerpt=str(exc),
                attempts=1,
                stage="preflight",
            ) from exc
    finally:
        try:
            result_queue.close()
        except Exception:
            pass
        try:
            result_queue.join_thread()
        except Exception:
            pass

    if not isinstance(payload, dict):
        raise ExtractorExecutionError(
            message="STAAD preflight returned an invalid payload.",
            retryable=False,
            error_code="staad_preflight_failed",
            attempts=1,
            stage="preflight",
        )

    if payload.get("ok") is True:
        return payload

    message = str(payload.get("error") or "STAAD preflight failed.")
    lower_message = message.lower()
    error_code = "staad_preflight_failed"
    retryable = True
    if "pywin32 is not available" in lower_message:
        error_code = "missing_dependency"
        retryable = False
    elif "could not find bentley.staad.exe" in lower_message or "openstaad is registered" in lower_message:
        error_code = "license_not_available"
    elif "non-interactive windows session" in lower_message or "run only when user is logged on" in lower_message:
        error_code = "non_interactive_session"
        retryable = False
    elif "geometry/output interfaces" in lower_message or "interactive desktop session" in lower_message:
        error_code = "license_not_available"
        retryable = False
    elif "failed to connect to staad.pro" in lower_message or "staad.pro does not appear to be running" in lower_message:
        error_code = "license_not_available"

    raise ExtractorExecutionError(
        message=message,
        retryable=retryable,
        error_code=error_code,
        stderr_excerpt=_safe_excerpt(message, settings.max_stderr_excerpt_chars),
        attempts=1,
        stage="preflight",
        hint=_build_failure_hint(message, error_code),
    )


def run_extractor_once(
    *,
    settings: Settings,
    std_file_path: Path,
    work_dir: Path,
    staadpro_path: Optional[Path],
    timeout_seconds: int,
    job_id: str,
    extraction_flow: str = "standard",
    generation_request_path: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    abort_check: Optional[Callable[[], bool]] = None,
    pid_callback: Optional[Callable[[Optional[int]], None]] = None,
) -> tuple[dict[str, Any], Optional[int]]:
    """
    Run the bundled STAAD max extractor once inside a dedicated worker process.

    ``staadpro_path`` is accepted for backward compatibility with older clients but is
    not used by the OpenSTAAD-based implementation.
    """
    del staadpro_path

    def _raise_if_aborted(*, attempts: int = 1) -> None:
        if abort_check is not None and abort_check():
            raise ExtractorExecutionError(
                message="STAAD job was aborted by client request.",
                retryable=False,
                error_code="canceled",
                attempts=attempts,
                stage="execution",
                hint="Submit a new job when ready; the previous run was force-stopped.",
            )

    _raise_if_aborted()

    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    outer_timeout = compute_outer_timeout_seconds(settings, per_attempt_staad_timeout=timeout_seconds)
    repo_root = Path(__file__).resolve().parent.parent.parent

    proc = ctx.Process(
        target=_run_extraction_child,
        kwargs={
            "queue": result_queue,
            "repo_root": str(repo_root),
            "std_file_path": str(std_file_path),
            "timeout_seconds": timeout_seconds,
            "include_combinations": settings.extractor_include_combinations,
            "poll_interval_seconds": settings.extractor_poll_interval_seconds,
            "launch_wait_seconds": settings.staad_launch_wait_seconds,
            "startup_timeout_seconds": settings.staad_startup_timeout_seconds,
            "attach_timeout_seconds": settings.staad_attach_timeout_seconds,
            "extraction_flow": extraction_flow,
            "generation_request_path": str(generation_request_path) if generation_request_path else None,
        },
        daemon=False,
    )

    if settings.keep_staad_warm:
        logger.info("job=%s skipping pre-attempt STAAD cleanup because KEEP_STAAD_WARM=true", job_id)
    else:
        pre_cleanup = kill_staad_processes_by_name(
            settings.staad_process_name_list(),
            reason=f"job={job_id} pre_attempt_cleanup",
        )
        if pre_cleanup:
            logger.warning("job=%s pre_attempt_cleanup killed_images=%d", job_id, pre_cleanup)

    logger.info(
        "job=%s starting managed extractor worker analysis_timeout=%ss startup_timeout=%ss attach_timeout=%ss",
        job_id,
        timeout_seconds,
        settings.staad_startup_timeout_seconds,
        settings.staad_attach_timeout_seconds,
    )
    started = time.monotonic()
    proc.start()
    worker_pid = proc.pid
    if pid_callback is not None:
        pid_callback(worker_pid)

    payload: Optional[dict[str, Any]] = None
    aborted = False

    def _handle_child_payload(candidate: Any) -> None:
        nonlocal payload
        if not isinstance(candidate, dict):
            payload = candidate
            return
        progress = candidate.get("progress")
        if isinstance(progress, dict):
            if progress_callback is not None:
                progress_callback(
                    int(progress.get("percent", 0)),
                    str(progress.get("message") or "Job in progress."),
                )
            return
        payload = candidate

    def _drain_child_queue() -> None:
        while True:
            try:
                candidate = result_queue.get_nowait()
            except Exception:
                return
            _handle_child_payload(candidate)
            if payload is not None:
                return

    def _force_kill_worker(*, reason: str) -> None:
        logger.error(
            "job=%s extractor worker %s; killing pid=%s",
            job_id,
            reason,
            worker_pid,
        )
        if worker_pid is not None:
            kill_process_tree_windows(worker_pid)
        proc.join(timeout=30)

    try:
        deadline = time.monotonic() + outer_timeout
        while payload is None and proc.is_alive():
            if abort_check is not None and abort_check():
                aborted = True
                _force_kill_worker(reason="aborted by client")
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            proc.join(timeout=min(0.25, remaining))
            _drain_child_queue()

        if payload is None and not proc.is_alive():
            deadline = time.monotonic() + 1.0
            while payload is None and time.monotonic() < deadline:
                try:
                    candidate = result_queue.get(timeout=0.1)
                except Exception:
                    continue
                _handle_child_payload(candidate)

        if aborted or (abort_check is not None and abort_check()):
            if proc.is_alive() and worker_pid is not None:
                _force_kill_worker(reason="aborted by client")
            raise ExtractorExecutionError(
                message="STAAD job was aborted by client request.",
                retryable=False,
                error_code="canceled",
                attempts=1,
                stage="execution",
                hint="Submit a new job when ready; the previous run was force-stopped.",
            )

        if payload is None and proc.is_alive():
            _force_kill_worker(reason=f"timed out after {outer_timeout:.1f}s")
            raise ExtractorExecutionError(
                message=f"STAAD execution timed out after {outer_timeout:.0f} seconds.",
                retryable=True,
                error_code="timeout",
                attempts=1,
                stage="execution",
                hint=_build_failure_hint("timeout", "timeout"),
            )

        if payload is None:
            raise ExtractorExecutionError(
                message=f"Extractor worker exited without returning data (exit={proc.exitcode}).",
                retryable=False,
                error_code="worker_no_result",
                stderr_excerpt="No payload was returned from the extractor worker.",
                attempts=1,
                stage="execution",
            )
    finally:
        if pid_callback is not None:
            pid_callback(None)
        try:
            result_queue.close()
        except Exception:
            pass
        try:
            result_queue.join_thread()
        except Exception:
            pass

        # Always clean STAAD on abort; otherwise honor KEEP_STAAD_WARM on success only.
        keep_warm = settings.keep_staad_warm and proc.exitcode == 0 and not aborted
        if abort_check is not None and abort_check():
            keep_warm = False
        if keep_warm:
            logger.info("job=%s keeping STAAD process warm after successful worker exit", job_id)
        else:
            n_killed = cleanup_staad_processes_referencing_job_dir(
                work_dir,
                settings.staad_process_name_list(),
            )
            if n_killed:
                logger.warning("job=%s post-run STAAD cleanup killed %s process(es)", job_id, n_killed)
            image_cleanup = kill_staad_processes_by_name(
                settings.staad_process_name_list(),
                reason=f"job={job_id} final_cleanup",
            )
            if image_cleanup:
                logger.warning("job=%s final_cleanup killed_images=%d", job_id, image_cleanup)

    elapsed = time.monotonic() - started
    logger.info("job=%s extractor worker finished exit=%s duration=%.2fs", job_id, proc.exitcode, elapsed)

    if not isinstance(payload, dict):
        raise ExtractorExecutionError(
            message="Extractor worker returned an invalid payload.",
            retryable=False,
            error_code="invalid_worker_payload",
            attempts=1,
            stage="execution",
        )

    if payload.get("ok") is True:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ExtractorExecutionError(
                message="Extractor worker returned invalid result JSON.",
                retryable=False,
                error_code="invalid_json_from_extractor",
                stderr_excerpt=_safe_excerpt(json.dumps(payload), settings.max_stderr_excerpt_chars),
                attempts=1,
                stage="execution",
            )
        return result, worker_pid

    message = str(payload.get("error") or "STAAD extractor execution failed.")
    err_api = _classify_failure(message, settings)
    details = {
        "exception_type": payload.get("error_type"),
    }
    traceback_excerpt = payload.get("traceback")
    if traceback_excerpt:
        details["traceback_excerpt"] = _safe_excerpt(str(traceback_excerpt), settings.max_stderr_excerpt_chars)
    raise ExtractorExecutionError(
        message=err_api.message,
        retryable=err_api.retryable,
        stderr_excerpt=details.get("traceback_excerpt") or err_api.last_stderr_excerpt,
        error_code=err_api.error_code,
        attempts=1,
        stage=err_api.stage,
        hint=err_api.hint,
        details=details,
    )


def run_extractor_with_retries(
    *,
    settings: Settings,
    std_file_path: Path,
    work_dir: Path,
    staadpro_path: Optional[Path],
    timeout_seconds: int,
    max_retries: int,
    retry_delay_seconds: int,
    job_id: str,
    extraction_flow: str = "standard",
    generation_request_path: Optional[Path] = None,
    cleanup_fn: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    abort_check: Optional[Callable[[], bool]] = None,
    pid_callback: Optional[Callable[[Optional[int]], None]] = None,
) -> tuple[dict[str, Any], int]:
    """
    Run the extractor with retries on transient failures.

    ``cleanup_fn`` is invoked after each failed attempt when provided.
    """
    if settings.analysis_backend == "inhouse":
        from app.services.inhouse_runner import run_inhouse
        return run_inhouse(settings=settings, std_file_path=std_file_path, timeout_seconds=timeout_seconds,
            extraction_flow=extraction_flow, generation_request_path=generation_request_path,
            progress_callback=progress_callback, abort_check=abort_check, pid_callback=pid_callback)

    attempts = 0
    last_error: Optional[ExtractorExecutionError] = None
    total_attempts = max_retries + 1

    def _raise_if_aborted() -> None:
        if abort_check is not None and abort_check():
            raise ExtractorExecutionError(
                message="STAAD job was aborted by client request.",
                retryable=False,
                error_code="canceled",
                attempts=max(1, attempts),
                stage="execution",
                hint="Submit a new job when ready; the previous run was force-stopped.",
            )

    with _acquire_staad_runtime_lock(settings, job_id=job_id):
        while attempts < total_attempts:
            _raise_if_aborted()
            attempts += 1
            attempt_started = time.monotonic()
            logger.info("job=%s attempt=%s/%s started", job_id, attempts, total_attempts)
            if progress_callback is not None:
                progress_callback(20, f"Starting STAAD extraction attempt {attempts} of {total_attempts}.")
            try:
                result, _pid = run_extractor_once(
                    settings=settings,
                    std_file_path=std_file_path,
                    work_dir=work_dir,
                    staadpro_path=staadpro_path,
                    timeout_seconds=timeout_seconds,
                    job_id=job_id,
                    extraction_flow=extraction_flow,
                    generation_request_path=generation_request_path,
                    progress_callback=progress_callback,
                    abort_check=abort_check,
                    pid_callback=pid_callback,
                )
                logger.info(
                    "job=%s attempt=%s/%s succeeded duration=%.2fs",
                    job_id,
                    attempts,
                    total_attempts,
                    time.monotonic() - attempt_started,
                )
                return result, attempts
            except ExtractorExecutionError as exc:
                last_error = exc
                exc.attempts = attempts
                logger.warning(
                    "job=%s attempt=%s/%s failed retryable=%s stage=%s error_code=%s duration=%.2fs message=%s",
                    job_id,
                    attempts,
                    total_attempts,
                    exc.retryable,
                    exc.stage,
                    exc.error_code,
                    time.monotonic() - attempt_started,
                    exc.message,
                )
                if cleanup_fn is not None:
                    try:
                        cleanup_fn()
                    except Exception:
                        logger.exception("job=%s cleanup_fn after failed attempt", job_id)
                if exc.error_code == "canceled" or not exc.retryable or attempts >= total_attempts:
                    break
                jitter = random.uniform(0, max(1.0, retry_delay_seconds * 0.25))
                sleep_seconds = retry_delay_seconds * (2 ** (attempts - 1)) + jitter
                logger.info("job=%s retrying_after=%.1fs attempt=%s/%s", job_id, sleep_seconds, attempts, total_attempts)
                if progress_callback is not None:
                    progress_callback(95, "Retrying after a transient STAAD failure.")
                sleep_deadline = time.monotonic() + sleep_seconds
                while time.monotonic() < sleep_deadline:
                    _raise_if_aborted()
                    remaining_sleep = sleep_deadline - time.monotonic()
                    if remaining_sleep <= 0:
                        break
                    time.sleep(min(0.25, remaining_sleep))

    assert last_error is not None
    raise last_error
