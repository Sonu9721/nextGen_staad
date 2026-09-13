from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.bootstrap import configure_runtime_environment
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.job_manager import JobManager
from app.schemas import ApiError, HealthCheckName, HealthResponse, JobAbortResponse, JobCreateResponse, JobStatusResponse
from app.services.health_checks import build_server_health, run_license_health_check, run_staad_readiness_check

_, _, logs_dir = configure_runtime_environment()
setup_logging(logs_dir)
settings = get_settings()

logger = logging.getLogger(__name__)
logger.info(
    "Application paths: TEMP=%s TMP=%s tempfile=%s jobs=%s logs=%s",
    os.environ.get("TEMP"),
    os.environ.get("TMP"),
    tempfile.gettempdir(),
    settings.staad_jobs_dir,
    settings.logs_dir,
)
if os.name == "nt":
    logger.info(
        "Windows session environment: USERNAME=%s SESSIONNAME=%s",
        os.environ.get("USERNAME"),
        os.environ.get("SESSIONNAME"),
    )

manager = JobManager(settings=settings)
app = FastAPI(title="STAAD Report Extractor API", version="1.0.0")
from app.console import router as console_router
app.include_router(console_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.on_event("startup")
async def on_startup() -> None:
    manager.run_startup_cleanup()
    await manager.start_worker()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await manager.shutdown()


def _normalize_staadpro_path(raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    staadpro = Path(raw)
    if not staadpro.is_file():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "invalid_staadpro_path",
                "message": f"STAAD executable not found: {staadpro}",
                "retryable": False,
            },
        )
    return staadpro


def _sanitize_api_error(error: Optional[ApiError]) -> Optional[ApiError]:
    if error is None:
        return None

    safe_details = {
        "error_code": error.error_code,
        "retryable": error.retryable,
        "attempts": error.attempts,
    }
    if error.stage:
        safe_details["stage"] = error.stage
    if error.hint:
        safe_details["hint"] = error.hint

    return error.model_copy(
        update={
            "details": safe_details,
            "last_stderr_excerpt": None,
        }
    )


@app.get("/health", response_model=HealthResponse)
async def health(check: HealthCheckName = Query(default="server")):
    if check == "server":
        try:
            await manager.start_worker()
            payload = build_server_health(manager=manager)
        except Exception as exc:
            payload = HealthResponse(
                check="server",
                status="error",
                healthy=False,
                message="FastAPI server responded, but worker readiness could not be confirmed.",
                details={"exception_type": exc.__class__.__name__},
            )
        return payload

    payload = run_license_health_check(settings=manager.settings) if check == "license" else run_staad_readiness_check(
        settings=manager.settings
    )
    status_code = status.HTTP_200_OK if payload.healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


@app.post("/jobs", response_model=JobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    std_file: UploadFile = File(...),
    staadpro_path: Optional[str] = Form(default=None),
    timeout_seconds: int = Form(default=settings.staad_timeout_seconds),
    max_retries: int = Form(default=settings.default_max_retries),
    retry_delay_seconds: int = Form(default=settings.default_retry_delay_seconds),
    extraction_flow: str = Form(default="standard"),
    generation_request: Optional[UploadFile] = File(default=None),
) -> JobCreateResponse:
    submission_started = time.perf_counter()
    if not (std_file.filename or "").lower().endswith(".std"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "invalid_input_file",
                "message": "Only .std file upload is supported.",
                "retryable": False,
            },
        )
    if timeout_seconds <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "invalid_timeout",
                "message": "timeout_seconds must be > 0",
                "retryable": False,
            },
        )
    if max_retries < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "invalid_max_retries",
                "message": "max_retries must be >= 0",
                "retryable": False,
            },
        )
    if retry_delay_seconds < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "invalid_retry_delay",
                "message": "retry_delay_seconds must be >= 1",
                "retryable": False,
            },
        )
    normalized_flow = extraction_flow if isinstance(extraction_flow, str) else "standard"
    normalized_flow = normalized_flow.strip().lower().replace("-", "_")
    if normalized_flow not in {
        "standard",
        "fully_unitized",
        "unitized",
        "fullyunitized",
        "casement",
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "invalid_extraction_flow",
                "message": "extraction_flow must be 'standard', 'fully_unitized', or 'casement'",
                "retryable": False,
            },
        )
    if normalized_flow in {"unitized", "fullyunitized"}:
        normalized_flow = "fully_unitized"

    if manager.settings.analysis_backend == "openstaad":
        _normalize_staadpro_path(staadpro_path)
    provisional_job_id = str(uuid4())
    logger.info(
        "job=%s submission received filename=%s timeout_seconds=%s max_retries=%s retry_delay_seconds=%s",
        provisional_job_id,
        std_file.filename,
        timeout_seconds,
        max_retries,
        retry_delay_seconds,
    )
    try:
        record = await manager.create_job(
            upload=std_file,
            job_id=provisional_job_id,
            staadpro_path=staadpro_path,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            extraction_flow=normalized_flow,
            generation_request=generation_request,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error_code": "file_too_large",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    finally:
        await std_file.close()

    submit_latency_seconds = time.perf_counter() - submission_started
    logger.info(
        "job=%s submission queued response_status=%s submit_latency_seconds=%.3f queue_size=%d",
        record.id,
        "queued",
        submit_latency_seconds,
        manager.queue.qsize(),
    )
    return JobCreateResponse(job_id=record.id, status="queued")


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    record = await manager.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "job_not_found",
                "message": f"Job not found: {job_id}",
                "retryable": False,
            },
        )
    return JobStatusResponse(
        job_id=record.id,
        status=record.status,
        progress_percent=record.progress_percent,
        message=record.message,
        attempts=record.attempts,
        max_attempts=record.max_attempts,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        result_ready=record.status == "succeeded",
        error=_sanitize_api_error(record.error),
        duration_seconds=record.duration_seconds,
        working_directory=str(record.temp_dir) if record.temp_dir else None,
        cleanup_performed=record.cleanup_performed,
    )


@app.post("/jobs/{job_id}/abort", response_model=JobAbortResponse)
async def abort_job(job_id: str) -> JobAbortResponse:
    record = await manager.abort_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "job_not_found",
                "message": f"Job not found: {job_id}",
                "retryable": False,
            },
        )

    if record.status == "canceled":
        message = record.message or "Job canceled."
    elif record.status == "running" and record.abort_requested:
        message = record.message or "Abort requested; stopping STAAD execution."
    else:
        message = f"Job already {record.status}."
    return JobAbortResponse(job_id=record.id, status=record.status, message=message)


@app.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str):
    result = await manager.get_result_response(job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "job_not_found",
                "message": f"Job not found: {job_id}",
                "retryable": False,
            },
        )
    if result.status in ("queued", "running"):
        return {
            "job_id": result.job_id,
            "status": result.status,
            "message": "Job in progress.",
            "working_directory": result.working_directory,
        }
    if result.status == "canceled":
        record = await manager.get_job(job_id)
        cancel_message = (
            record.message
            if record is not None and record.message
            else "Job canceled."
        )
        return {
            "job_id": result.job_id,
            "status": result.status,
            "message": cancel_message,
            "working_directory": result.working_directory,
            "cleanup_performed": result.cleanup_performed,
        }
    if result.status == "failed":
        safe_error = _sanitize_api_error(result.error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "job_id": result.job_id,
                "status": result.status,
                "error": safe_error.message if safe_error else "Job failed.",
                "error_code": safe_error.error_code if safe_error else "job_failed",
                "retryable": safe_error.retryable if safe_error else False,
                "stage": safe_error.stage if safe_error else None,
                "hint": safe_error.hint if safe_error else None,
                "details": safe_error.details if safe_error else None,
                "cleanup_performed": result.cleanup_performed,
                "duration_seconds": result.duration_seconds,
                "working_directory": result.working_directory,
                "attempts": result.attempts,
            },
        )
    data = result.result
    return {
        "job_id": result.job_id,
        "status": "succeeded",
        "duration_seconds": result.duration_seconds,
        "working_directory": result.working_directory,
        "cleanup_performed": result.cleanup_performed,
        "result": data,
        "attempts": result.attempts,
        "max_attempts": result.max_attempts,
    }
