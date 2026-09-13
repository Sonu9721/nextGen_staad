from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import Settings
from app.services.job_cleanup import cleanup_old_job_folders
from app.services.staad_process import (
    cleanup_staad_processes_referencing_job_dir,
    kill_process_tree_windows,
    kill_staad_processes_by_name,
)
from app.services.staad_runner import (
    ExtractorExecutionError,
    cleanup_job_artifacts_with_retries,
    run_extractor_with_retries,
)
from app.schemas import ApiError, JobResultResponse, JobStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class JobRecord:
    id: str
    status: JobStatus
    attempts: int
    max_attempts: int
    created_at: datetime
    progress_percent: int = 0
    message: str = "Job is not started yet."
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[ApiError] = None
    temp_dir: Optional[Path] = None
    requested_file_name: Optional[str] = None
    duration_seconds: Optional[float] = None
    cleanup_performed: bool = False
    std_file: Optional[Path] = None
    staadpro_path: Optional[Path] = None
    timeout_seconds: int = 0
    retry_delay_seconds: int = 0
    extraction_flow: str = "standard"
    generation_request_file: Optional[Path] = None
    extractor_pid: Optional[int] = None
    abort_requested: bool = False
    model_geometry: Optional[dict[str, Any]] = None


@dataclass
class JobManager:
    """In-memory job registry with a single background STAAD worker."""

    settings: Settings
    jobs: dict[str, JobRecord] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    queue: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue, repr=False)
    worker_tasks: list[asyncio.Task[None]] = field(default_factory=list, init=False, repr=False)
    maintenance_task: Optional[asyncio.Task[None]] = field(default=None, init=False, repr=False)
    _worker_warning_emitted: bool = field(default=False, init=False, repr=False)

    def _set_job_progress_nowait(self, job_id: str, progress_percent: int, message: str) -> None:
        record = self.jobs.get(job_id)
        if record is None:
            return

        percent = max(0, min(100, int(progress_percent)))
        if record.status not in ("succeeded", "failed", "canceled"):
            percent = min(percent, 99)

        if percent < record.progress_percent:
            return

        record.progress_percent = percent
        record.message = message

    async def update_job_progress(self, job_id: str, progress_percent: int, message: str) -> None:
        async with self.lock:
            self._set_job_progress_nowait(job_id, progress_percent, message)

    async def prune_finished_jobs(self) -> None:
        """Trim completed/failed job metadata after the configured TTL to cap memory use."""
        ttl_seconds = self.settings.completed_job_ttl_seconds
        if ttl_seconds <= 0:
            return

        cutoff_timestamp = _utcnow().timestamp() - float(ttl_seconds)
        async with self.lock:
            stale_ids = [
                job_id
                for job_id, record in self.jobs.items()
                if record.finished_at is not None
                and record.status in ("succeeded", "failed", "canceled")
                and record.finished_at.timestamp() <= cutoff_timestamp
            ]
            for job_id in stale_ids:
                self.jobs.pop(job_id, None)

        if stale_ids:
            logger.info("Pruned %d completed job record(s) from memory", len(stale_ids))

    def run_startup_cleanup(self) -> None:
        """Delete stale job folders and log counts (called once from FastAPI startup)."""
        if self.settings.job_retention_hours <= 0:
            logger.info("Job retention disabled (JOB_RETENTION_HOURS <= 0); skipping stale folder cleanup.")
            return
        stats = cleanup_old_job_folders(
            self.settings.staad_jobs_dir,
            self.settings.job_retention_hours,
        )
        logger.info(
            "Startup stale job cleanup: deleted=%d skipped=%d errors=%d",
            stats.deleted,
            stats.skipped,
            len(stats.errors),
        )
        for err in stats.errors[:20]:
            logger.warning("Startup cleanup error: %s", err)

    def effective_worker_count(self) -> int:
        worker_count = max(1, int(self.settings.staad_worker_count))
        if not self.settings.allow_parallel_staad:
            if worker_count > 1 and not self._worker_warning_emitted:
                logger.warning(
                    "ALLOW_PARALLEL_STAAD is disabled; forcing STAAD_WORKER_COUNT=%d down to 1 for safe serialization",
                    worker_count,
                )
                self._worker_warning_emitted = True
            return 1
        return worker_count

    def maintenance_interval_seconds(self) -> Optional[float]:
        ttl_seconds = int(self.settings.completed_job_ttl_seconds)
        if ttl_seconds <= 0:
            return None
        return float(max(5, min(ttl_seconds, 60)))

    async def _maintenance_loop(self) -> None:
        interval_seconds = self.maintenance_interval_seconds()
        if interval_seconds is None:
            return

        logger.info("STAAD maintenance loop started prune_interval_seconds=%.1f", interval_seconds)
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    await self.prune_finished_jobs()
                except Exception:
                    logger.exception("STAAD maintenance prune cycle failed")
        except asyncio.CancelledError:
            raise
        finally:
            logger.info("STAAD maintenance loop stopped")

    async def start_maintenance(self) -> None:
        interval_seconds = self.maintenance_interval_seconds()
        if interval_seconds is None:
            return
        if self.maintenance_task is not None and not self.maintenance_task.done():
            return
        self.maintenance_task = asyncio.create_task(self._maintenance_loop(), name="staad-maintenance-loop")

    async def start_worker(self) -> None:
        """Ensure the configured background STAAD worker loop(s) are running."""
        await self.start_maintenance()
        active_tasks = [task for task in self.worker_tasks if not task.done()]
        self.worker_tasks = active_tasks

        worker_count = self.effective_worker_count()
        if len(self.worker_tasks) >= worker_count:
            return

        starting = worker_count - len(self.worker_tasks)
        for index in range(len(self.worker_tasks), worker_count):
            task = asyncio.create_task(self._worker_loop(), name=f"staad-job-worker-{index + 1}")
            self.worker_tasks.append(task)
        logger.info(
            "STAAD background worker(s) started requested=%d started=%d active=%d parallel=%s",
            worker_count,
            starting,
            len(self.worker_tasks),
            self.settings.allow_parallel_staad,
        )

    async def shutdown(self) -> None:
        """Stop the background STAAD worker(s) gracefully."""
        maintenance_task = self.maintenance_task
        self.maintenance_task = None
        if maintenance_task is not None:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass

        tasks = [task for task in self.worker_tasks if not task.done()]
        self.worker_tasks = tasks
        if not tasks:
            return
        for _ in tasks:
            await self.queue.put(None)
        await asyncio.gather(*tasks, return_exceptions=False)
        self.worker_tasks = []
        logger.info("STAAD background worker(s) stopped count=%d", len(tasks))

    async def create_job(
        self,
        *,
        upload: UploadFile,
        job_id: Optional[str] = None,
        staadpro_path: Optional[str],
        timeout_seconds: int,
        max_retries: int,
        retry_delay_seconds: int,
        extraction_flow: str = "standard",
        generation_request: Optional[UploadFile] = None,
    ) -> JobRecord:
        """
        Accept an upload, create a unique job directory under ``STAAD_JOBS_DIR``, and
        enqueue background execution.
        """
        await self.start_worker()

        job_id = job_id or str(uuid4())
        job_root = self.settings.staad_jobs_dir / f"job_{job_id}"
        job_root.mkdir(parents=True, exist_ok=True)
        logger.info(
            "job=%s accepted filename=%s timeout_seconds=%s max_retries=%s retry_delay_seconds=%s extraction_flow=%s",
            job_id,
            upload.filename,
            timeout_seconds,
            max_retries,
            retry_delay_seconds,
            extraction_flow,
        )

        try:
            stage_started = time.perf_counter()
            # Browser/client filenames must never escape the per-job directory.
            filename = (upload.filename or "input.std").replace("\\", "/").rsplit("/", 1)[-1]
            filename = filename.replace(":", "_") or "input.std"
            target_std = job_root / filename
            if target_std.suffix.lower() != ".std":
                target_std = target_std.with_suffix(".std")

            total_size = 0
            with target_std.open("wb") as f:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > self.settings.max_upload_size_bytes:
                        raise ValueError(
                            f"Uploaded file too large. Max {self.settings.max_upload_size_bytes} bytes."
                        )
                    f.write(chunk)
            logger.info(
                "job=%s staging completed bytes=%d staging_latency_seconds=%.3f path=%s",
                job_id,
                total_size,
                time.perf_counter() - stage_started,
                target_std,
            )

            generation_request_path: Optional[Path] = None
            if generation_request is not None and generation_request.filename:
                generation_request_path = job_root / "generation_request.json"
                request_size = 0
                with generation_request_path.open("wb") as request_file:
                    while True:
                        chunk = await generation_request.read(1024 * 1024)
                        if not chunk:
                            break
                        request_size += len(chunk)
                        if request_size > self.settings.max_upload_size_bytes:
                            raise ValueError(
                                f"Uploaded generation request too large. Max {self.settings.max_upload_size_bytes} bytes."
                            )
                        request_file.write(chunk)
                await generation_request.close()
                logger.info(
                    "job=%s generation request staged bytes=%d path=%s",
                    job_id,
                    request_size,
                    generation_request_path,
                )
        except Exception:
            shutil.rmtree(job_root, ignore_errors=True)
            raise

        record = JobRecord(
            id=job_id,
            status="queued",
            attempts=0,
            max_attempts=max_retries + 1,
            created_at=_utcnow(),
            temp_dir=job_root,
            requested_file_name=upload.filename,
            std_file=target_std,
            staadpro_path=Path(staadpro_path) if staadpro_path else None,
            timeout_seconds=timeout_seconds,
            retry_delay_seconds=retry_delay_seconds,
            extraction_flow=extraction_flow,
            generation_request_file=generation_request_path,
        )

        async with self.lock:
            self.jobs[job_id] = record

        await self.queue.put(job_id)
        logger.info("job=%s queued queue_size=%d", job_id, self.queue.qsize())
        return record

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                if job_id is None:
                    return
                rec = await self.get_job(job_id)
                if rec is None:
                    continue
                if rec.status == "canceled":
                    logger.info("job=%s skipped because it was canceled before execution", job_id)
                    continue

                std_file = rec.std_file
                timeout_seconds = rec.timeout_seconds or self.settings.staad_timeout_seconds
                retry_delay_seconds = rec.retry_delay_seconds or self.settings.default_retry_delay_seconds
                staadpro_path = rec.staadpro_path
                max_retries = max(0, rec.max_attempts - 1)

                if std_file is None:
                    logger.error("job=%s missing staged input file metadata", job_id)
                    rec.status = "failed"
                    rec.finished_at = _utcnow()
                    rec.progress_percent = 100
                    rec.message = "Job failed."
                    rec.error = ApiError(
                        error_code="missing_input_file",
                        message="Job metadata was incomplete before worker execution.",
                        retryable=False,
                        attempts=rec.attempts,
                        stage="queue",
                    )
                    continue

                logger.info("job=%s dequeued queue_size=%d", job_id, self.queue.qsize())
                await self._execute_job(
                    job_id=job_id,
                    std_file=Path(std_file),
                    staadpro_path=Path(staadpro_path) if staadpro_path else None,
                    timeout_seconds=int(timeout_seconds),
                    max_retries=max_retries,
                    retry_delay_seconds=int(retry_delay_seconds),
                )
            except Exception:
                logger.exception("STAAD worker loop failure while handling job=%s", job_id)
            finally:
                self.queue.task_done()

    async def _execute_job(
        self,
        *,
        job_id: str,
        std_file: Path,
        staadpro_path: Optional[Path],
        timeout_seconds: int,
        max_retries: int,
        retry_delay_seconds: int,
    ) -> None:
        rec = await self.get_job(job_id)
        if rec is None:
            return

        async with self.lock:
            if rec.status == "canceled":
                logger.info("job=%s execution skipped because job was canceled", job_id)
                return
            rec.status = "running"
            if rec.started_at is None:
                rec.started_at = _utcnow()
            rec.error = None
            rec.progress_percent = max(rec.progress_percent, 10)
            rec.message = "Job started."

        work_dir = std_file.parent
        t_job = time.monotonic()
        cleanup_done = False
        logger.info(
            "job=%s started timeout_seconds=%s max_attempts=%s work_dir=%s",
            job_id,
            timeout_seconds,
            rec.max_attempts,
            work_dir,
        )

        def _artifact_cleanup() -> None:
            """Between retries: strip generated files, keep .std and optional .anl."""
            cleanup_job_artifacts_with_retries(
                work_dir,
                std_path=std_file,
                keep_anl=self.settings.keep_anl_file,
                retry_seconds=self.settings.cleanup_retry_seconds,
                retry_interval_seconds=self.settings.cleanup_retry_interval_seconds,
            )

        loop = asyncio.get_running_loop()

        def _progress_callback(progress_percent: int, message: str) -> None:
            loop.call_soon_threadsafe(self._set_job_progress_nowait, job_id, progress_percent, message)

        def _set_extractor_pid(pid: Optional[int]) -> None:
            loop.call_soon_threadsafe(self._set_extractor_pid_nowait, job_id, pid)

        def _abort_check() -> bool:
            record = self.jobs.get(job_id)
            return record is not None and record.abort_requested

        try:
            if self.settings.job_retention_hours > 0:
                stats = cleanup_old_job_folders(
                    self.settings.staad_jobs_dir,
                    self.settings.job_retention_hours,
                )
                if stats.deleted or stats.errors:
                    logger.info(
                        "job=%s pre_execution_cleanup deleted=%d skipped=%d errors=%d",
                        job_id,
                        stats.deleted,
                        stats.skipped,
                        len(stats.errors),
                    )
            await self.update_job_progress(job_id, 20, "Preparing structural analysis." if self.settings.analysis_backend == "inhouse" else "Preparing STAAD extraction.")
            result, attempts = await asyncio.to_thread(
                run_extractor_with_retries,
                settings=self.settings,
                std_file_path=std_file,
                work_dir=work_dir,
                staadpro_path=staadpro_path,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                retry_delay_seconds=retry_delay_seconds,
                job_id=job_id,
                extraction_flow=rec.extraction_flow,
                generation_request_path=rec.generation_request_file,
                cleanup_fn=_artifact_cleanup,
                progress_callback=_progress_callback,
                abort_check=_abort_check,
                pid_callback=_set_extractor_pid,
            )
            if rec.abort_requested:
                rec.attempts = attempts
                rec.result = None
                rec.status = "canceled"
                rec.progress_percent = 100
                rec.message = "Job canceled during execution."
                rec.error = None
                logger.info("job=%s canceled after extractor returned because abort was requested", job_id)
            else:
                await self.update_job_progress(job_id, 98, "Finalizing job.")
                rec.attempts = attempts
                rec.result = result
                rec.status = "succeeded"
                rec.progress_percent = 100
                rec.message = "Job completed successfully."
                logger.info("job=%s extractor result recorded attempts=%s", job_id, attempts)
        except ExtractorExecutionError as exc:
            rec.progress_percent = max(rec.progress_percent, 98)
            rec.message = "Finalizing job."
            rec.attempts = exc.attempts
            if self.settings.analysis_backend == "inhouse" and exc.error_code == "SINGULAR_MATRIX" and not rec.abort_requested:
                # Keep only review geometry in the existing in-memory job record;
                # failed-job files still follow the original cleanup policy.
                from app.services.model_geometry import read_model_geometry
                try:
                    rec.model_geometry = await asyncio.to_thread(read_model_geometry, std_file)
                except Exception:
                    logger.warning("job=%s could not retain review geometry", job_id, exc_info=True)
            if exc.error_code == "canceled" or rec.abort_requested:
                rec.status = "canceled"
                rec.progress_percent = 100
                rec.message = "Job canceled during execution."
                rec.error = None
                logger.info(
                    "job=%s canceled during execution attempts=%s error_code=%s",
                    job_id,
                    exc.attempts,
                    exc.error_code,
                )
            else:
                rec.status = "failed"
                rec.progress_percent = 100
                rec.message = "Job failed."
                rec.error = ApiError(
                    error_code=exc.error_code,
                    message=exc.message,
                    retryable=exc.retryable,
                    attempts=rec.attempts,
                    stage=exc.stage,
                    hint=exc.hint,
                    details=exc.details or None,
                    last_stderr_excerpt=exc.stderr_excerpt,
                )
                logger.warning(
                    "job=%s failed error_code=%s retryable=%s stage=%s attempts=%s message=%s",
                    job_id,
                    exc.error_code,
                    exc.retryable,
                    exc.stage,
                    exc.attempts,
                    exc.message,
                )
        except Exception as exc:
            if rec.abort_requested:
                rec.progress_percent = 100
                rec.message = "Job canceled during execution."
                rec.status = "canceled"
                rec.attempts = max(1, rec.attempts)
                rec.error = None
                logger.info("job=%s canceled after unexpected error because abort was requested", job_id)
            else:
                rec.progress_percent = max(rec.progress_percent, 98)
                rec.message = "Finalizing job."
                rec.status = "failed"
                rec.attempts = max(1, rec.attempts)
                rec.progress_percent = 100
                rec.message = "Job failed."
                rec.error = ApiError(
                    error_code="internal_error",
                    message="Internal execution error. See server logs for details.",
                    retryable=False,
                    attempts=rec.attempts,
                    stage="execution",
                    details={"exception_type": exc.__class__.__name__},
                )
                logger.exception("job=%s unexpected failure", job_id)
        finally:
            rec.extractor_pid = None
            rec.duration_seconds = time.monotonic() - t_job
            rec.finished_at = _utcnow()
            if rec.status not in ("succeeded", "failed", "canceled"):
                rec.progress_percent = max(rec.progress_percent, 98)
                rec.message = "Finalizing job."

            # Always trim artifacts; keep .std and optionally .anl
            try:
                cleanup_job_artifacts_with_retries(
                    work_dir,
                    std_path=std_file,
                    keep_anl=self.settings.keep_anl_file,
                    retry_seconds=self.settings.cleanup_retry_seconds,
                    retry_interval_seconds=self.settings.cleanup_retry_interval_seconds,
                )
                cleanup_done = True
                logger.info("job=%s cleanup_artifacts succeeded", job_id)
            except OSError as e:
                logger.warning("job=%s artifact cleanup failed: %s", job_id, e)

            rec.cleanup_performed = cleanup_done

            # Remove job folder entirely on cancel/error when configured
            if rec.temp_dir is not None:
                should_remove = False
                if rec.status == "canceled":
                    should_remove = True
                elif rec.status == "failed" and not self.settings.keep_job_folder_on_error:
                    should_remove = True
                if should_remove:
                    try:
                        shutil.rmtree(rec.temp_dir, ignore_errors=True)
                        logger.info("job=%s removed job folder after %s", job_id, rec.status)
                    except OSError as e:
                        logger.warning("job=%s could not remove job folder: %s", job_id, e)
                    rec.temp_dir = None
                    rec.std_file = None

            logger.info(
                "job=%s finished status=%s attempts=%s duration_seconds=%.2f cleanup_performed=%s",
                job_id,
                rec.status,
                rec.attempts,
                rec.duration_seconds,
                rec.cleanup_performed,
            )

    def _set_extractor_pid_nowait(self, job_id: str, pid: Optional[int]) -> None:
        record = self.jobs.get(job_id)
        if record is None:
            return
        record.extractor_pid = pid

    def _force_stop_running_job_processes(
        self,
        *,
        job_id: str,
        extractor_pid: Optional[int],
        work_dir: Optional[Path],
    ) -> None:
        """Kill the extractor child and STAAD processes for a running abort."""
        if self.settings.analysis_backend == "inhouse":
            # The in-house runner observes abort_requested and owns its child handle.
            return
        if extractor_pid is not None:
            logger.warning("job=%s abort killing extractor process tree pid=%s", job_id, extractor_pid)
            kill_process_tree_windows(extractor_pid)

        process_names = self.settings.staad_process_name_list()
        if work_dir is not None:
            n_killed = cleanup_staad_processes_referencing_job_dir(work_dir, process_names)
            if n_killed:
                logger.warning(
                    "job=%s abort killed %s STAAD process(es) referencing job dir",
                    job_id,
                    n_killed,
                )
        image_cleanup = kill_staad_processes_by_name(
            process_names,
            reason=f"job={job_id} abort_running",
        )
        if image_cleanup:
            logger.warning("job=%s abort killed_images=%d", job_id, image_cleanup)

    async def abort_job(self, job_id: str) -> Optional[JobRecord]:
        """
        Cancel a queued job, or force-stop a running STAAD job so the queue can advance.

        Queued jobs are marked canceled immediately. Running jobs set ``abort_requested``,
        kill the extractor/STAAD processes, then wait briefly for the worker to finalize.
        """
        kill_pid: Optional[int] = None
        kill_work_dir: Optional[Path] = None
        mode: str

        async with self.lock:
            rec = self.jobs.get(job_id)
            if rec is None:
                return None
            if rec.status == "queued":
                mode = "queued"
                rec.status = "canceled"
                rec.finished_at = _utcnow()
                rec.duration_seconds = (rec.finished_at - rec.created_at).total_seconds()
                rec.progress_percent = 100
                rec.message = "Job canceled before execution."
                rec.error = None
                cleanup_target = rec.temp_dir
            elif rec.status == "running":
                mode = "running"
                if not rec.abort_requested:
                    rec.abort_requested = True
                    rec.message = "Abort requested; stopping analysis."
                kill_pid = rec.extractor_pid
                kill_work_dir = rec.temp_dir
                cleanup_target = None
            else:
                return rec

        if mode == "queued":
            cleanup_done = False
            if cleanup_target is not None:
                shutil.rmtree(cleanup_target, ignore_errors=True)
                cleanup_done = not cleanup_target.exists()

            async with self.lock:
                current = self.jobs.get(job_id)
                if current is not None and current.status == "canceled":
                    current.cleanup_performed = cleanup_done
                    if cleanup_done:
                        current.temp_dir = None
                        current.std_file = None
                    logger.info(
                        "job=%s canceled cleanup_performed=%s queue_size=%d",
                        job_id,
                        current.cleanup_performed,
                        self.queue.qsize(),
                    )
                    return current
                return current

        await asyncio.to_thread(
            self._force_stop_running_job_processes,
            job_id=job_id,
            extractor_pid=kill_pid,
            work_dir=kill_work_dir,
        )

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            current = await self.get_job(job_id)
            if current is None:
                return None
            if current.status in ("canceled", "succeeded", "failed"):
                logger.info(
                    "job=%s abort finalized status=%s queue_size=%d",
                    job_id,
                    current.status,
                    self.queue.qsize(),
                )
                return current
            await asyncio.sleep(0.05)

        current = await self.get_job(job_id)
        logger.warning(
            "job=%s abort requested but job has not reached a terminal state yet status=%s",
            job_id,
            None if current is None else current.status,
        )
        return current

    async def abort_queued_job(self, job_id: str) -> Optional[JobRecord]:
        """Backward-compatible alias for :meth:`abort_job`."""
        return await self.abort_job(job_id)

    async def get_job(self, job_id: str) -> Optional[JobRecord]:
        async with self.lock:
            return self.jobs.get(job_id)

    async def get_result_response(self, job_id: str) -> Optional[JobResultResponse]:
        record = await self.get_job(job_id)
        if record is None:
            return None
        res = record.result if record.status == "succeeded" else None
        err = record.error
        wd = str(record.temp_dir) if record.temp_dir is not None else None
        return JobResultResponse(
            job_id=record.id,
            status=record.status,
            result=res,
            error=err,
            attempts=record.attempts,
            max_attempts=record.max_attempts,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            duration_seconds=record.duration_seconds,
            working_directory=wd,
            cleanup_performed=record.cleanup_performed,
        )
