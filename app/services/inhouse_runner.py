"""Bounded numerical worker. Never imports or calls Bentley process controls."""

import multiprocessing
import queue
import time
from engine.worker import analyze_job


def run_inhouse(
    *,
    settings,
    std_file_path,
    timeout_seconds,
    extraction_flow="standard",
    generation_request_path=None,
    progress_callback=None,
    abort_check=None,
    pid_callback=None,
):
    from app.services.staad_runner import ExtractorExecutionError

    ctx = multiprocessing.get_context("spawn")
    channel = ctx.Queue()
    event = ctx.Event()
    process = ctx.Process(
        target=analyze_job,
        args=(
            channel,
            event,
            str(std_file_path),
            extraction_flow,
            settings.extractor_include_combinations,
            str(generation_request_path) if generation_request_path else None,
        ),
    )
    deadline = time.monotonic() + timeout_seconds
    payload = None
    cancel_at = None
    started = False
    try:
        if abort_check and abort_check():
            raise ExtractorExecutionError(
                "Analysis canceled.", False, error_code="canceled", stage="analysis"
            )
        process.start()
        started = True
        if pid_callback:
            pid_callback(process.pid)
        while payload is None:
            if abort_check and abort_check():
                event.set()
                if cancel_at is None:
                    cancel_at = time.monotonic()
                if not process.is_alive() or time.monotonic() - cancel_at > 0.75:
                    raise ExtractorExecutionError(
                        "Analysis canceled.",
                        False,
                        error_code="canceled",
                        stage="analysis",
                    )
            if time.monotonic() > deadline:
                event.set()
                raise ExtractorExecutionError(
                    f"In-house analysis exceeded {timeout_seconds} seconds.",
                    False,
                    error_code="timeout",
                    stage="analysis",
                )
            try:
                item = channel.get(timeout=0.05)
            except queue.Empty:
                if not process.is_alive():
                    try:
                        item = channel.get(timeout=0.2)
                    except queue.Empty:
                        raise ExtractorExecutionError(
                            f"Numerical worker exited without a result (exit {process.exitcode}).",
                            False,
                            error_code="worker_no_result",
                            stage="execution",
                        )
                else:
                    continue
            if "progress" in item:
                if progress_callback:
                    progress_callback(*item["progress"])
            else:
                payload = item
        if event.is_set() or (abort_check and abort_check()):
            raise ExtractorExecutionError(
                "Analysis canceled.", False, error_code="canceled", stage="analysis"
            )
        if "error" in payload:
            code = payload["code"]
            raise ExtractorExecutionError(
                payload["error"],
                False,
                error_code="canceled" if code == "ANALYSIS_CANCELLED" else code,
                stage=payload["stage"],
            )
        return payload["result"], 1
    finally:
        if started:
            event.set()
            process.join(timeout=0.25)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()
        if pid_callback:
            pid_callback(None)
        channel.close()
        channel.join_thread()
