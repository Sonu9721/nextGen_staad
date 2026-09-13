from __future__ import annotations

import asyncio
import io
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

import app.main as main_module
from app.core.config import Settings
from app.job_manager import JobManager, JobRecord
from app.main import _normalize_staadpro_path
from app.schemas import JobResultResponse
from app.services.health_checks import run_license_health_check
from app.services.staad_runner import ExtractorExecutionError, _classify_failure, cleanup_job_artifacts_with_retries, run_staad_preflight


def make_settings(base: Path, **overrides: object) -> Settings:
    values = {
        "ANALYSIS_BACKEND": "openstaad",
        "TEMP_DIR": str(base / "temp"),
        "STAAD_JOBS_DIR": str(base / "jobs"),
        "LOGS_DIR": str(base / "logs"),
        "JOB_RETENTION_HOURS": 0,
        "COMPLETED_JOB_TTL_SECONDS": 3600,
    }
    values.update(overrides)
    return Settings(**values)


def success_result() -> dict[str, object]:
    return {
        "file": r"C:\STAADJobs\model.std",
        "analysis": {
            "load_case_mode": "all",
            "load_cases": [1, 2],
            "moment_mode": "internal-envelope",
            "include_combinations": True,
        },
        "units": {
            "output": {"force": "kN", "length": "mm", "moment": "kN-m"},
            "staad_detected": {"force": "KN", "length": "METER", "moment": "KN-meter"},
            "file_detected": {"force": "KN", "length": "METER", "moment": "KN-meter"},
        },
        "properties": {
            "bending_moment": {"unit": "kN-m", "major": {"value": 10.0, "axis": "MZ"}},
            "shear_force": {
                "unit": "kN",
                "major": {"value": 6.0, "axis": "FY", "member_id": 3},
                "minor": {
                    "axis": "FZ",
                    "max_on_mullions": {"value": 8.5, "member_id": 7},
                    "at_major_governing_point": {
                        "value": 2.25,
                        "member_id": 3,
                        "governing_load_case": 5,
                        "reference_major_load_case": 4,
                        "reference_major_value": 6.0,
                    },
                },
            },
            "axial_force": {"unit": "kN", "value": 1.0, "axis": "FX"},
            "displacement": {"unit": "mm", "value": 2.0, "direction": "RESULTANT"},
        },
    }


class MainHelpersTests(TestCase):
    def test_normalize_staadpro_path_allows_missing_value(self) -> None:
        self.assertIsNone(_normalize_staadpro_path(None))

    def test_normalize_staadpro_path_rejects_invalid_file(self) -> None:
        with self.assertRaises(HTTPException) as exc_info:
            _normalize_staadpro_path(r"C:\does-not-exist\Bentley.Staad.exe")

        self.assertEqual(exc_info.exception.status_code, 422)
        self.assertEqual(exc_info.exception.detail["error_code"], "invalid_staadpro_path")


class JobManagerRetentionTests(IsolatedAsyncioTestCase):
    async def test_prune_finished_jobs_removes_stale_records(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = make_settings(base, COMPLETED_JOB_TTL_SECONDS=1)
            manager = JobManager(settings=settings)
            manager.jobs["old-job"] = JobRecord(
                id="old-job",
                status="succeeded",
                attempts=1,
                max_attempts=1,
                created_at=datetime.now(timezone.utc) - timedelta(hours=1),
                finished_at=datetime.now(timezone.utc) - timedelta(seconds=5),
            )

            await manager.prune_finished_jobs()

            self.assertNotIn("old-job", manager.jobs)

    async def test_worker_processes_jobs_serially_from_queue(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = make_settings(base)
            manager = JobManager(settings=settings)
            gate = threading.Lock()
            state = {
                "active": 0,
                "max_active": 0,
                "job_ids": [],
            }

            def fake_run_extractor_with_retries(**kwargs):
                with gate:
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
                    state["job_ids"].append(kwargs["job_id"])
                time.sleep(0.05)
                with gate:
                    state["active"] -= 1
                return success_result(), 1

            async def wait_for_completion(job_id: str) -> None:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    record = await manager.get_job(job_id)
                    if record is not None and record.status == "succeeded":
                        return
                    await asyncio.sleep(0.01)
                self.fail(f"Job {job_id} did not complete in time")

            upload_a = UploadFile(filename="first.std", file=io.BytesIO(b"STAAD SPACE"))
            upload_b = UploadFile(filename="second.std", file=io.BytesIO(b"STAAD SPACE"))

            try:
                with (
                    patch("app.job_manager.run_extractor_with_retries", side_effect=fake_run_extractor_with_retries),
                ):
                    record_a = await manager.create_job(
                        upload=upload_a,
                        staadpro_path=None,
                        timeout_seconds=30,
                        max_retries=0,
                        retry_delay_seconds=1,
                    )
                    record_b = await manager.create_job(
                        upload=upload_b,
                        staadpro_path=None,
                        timeout_seconds=30,
                        max_retries=0,
                        retry_delay_seconds=1,
                    )
                    await wait_for_completion(record_a.id)
                    await wait_for_completion(record_b.id)

                self.assertEqual(state["max_active"], 1)
                self.assertEqual(len(state["job_ids"]), 2)
            finally:
                await manager.shutdown()

    async def test_worker_count_is_forced_to_one_when_parallel_is_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = make_settings(base, STAAD_WORKER_COUNT=3, ALLOW_PARALLEL_STAAD=False)
            manager = JobManager(settings=settings)

            try:
                with self.assertLogs("app.job_manager", level="WARNING") as captured:
                    await manager.start_worker()
                self.assertEqual(manager.effective_worker_count(), 1)
                self.assertEqual(len(manager.worker_tasks), 1)
                self.assertTrue(any("forcing STAAD_WORKER_COUNT=3 down to 1" in line for line in captured.output))
            finally:
                await manager.shutdown()


class ResponseShapeTests(TestCase):
    def test_job_result_response_has_no_legacy_results_field(self) -> None:
        self.assertNotIn("results", JobResultResponse.model_fields)


class CleanupRetryTests(TestCase):
    def test_cleanup_retries_locked_files_then_succeeds(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            std_path = base / "model.std"
            locked_path = base / "model.dbs"
            std_path.write_text("STAAD SPACE", encoding="utf-8")
            locked_path.write_text("artifact", encoding="utf-8")

            attempts = {"count": 0}
            original_unlink = Path.unlink

            def flaky_unlink(path_obj: Path, missing_ok: bool = False):
                if Path(path_obj) == locked_path and attempts["count"] < 2:
                    attempts["count"] += 1
                    exc = OSError(13, "The process cannot access the file because it is being used by another process")
                    exc.winerror = 32
                    raise exc
                return original_unlink(path_obj, missing_ok=missing_ok)

            with patch.object(Path, "unlink", autospec=True, side_effect=flaky_unlink):
                cleanup_job_artifacts_with_retries(
                    base,
                    std_path=std_path,
                    keep_anl=False,
                    retry_seconds=1.0,
                    retry_interval_seconds=0.01,
                )

            self.assertFalse(locked_path.exists())


class PreflightTests(TestCase):
    def test_preflight_can_be_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = make_settings(base, ENABLE_STAAD_PREFLIGHT=False)

            result = run_staad_preflight(settings=settings)

            self.assertEqual(result["status"], "skipped")


class LicenseHealthCheckTests(TestCase):
    def test_license_health_reports_success(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            payload = {
                "desktop_api_installed": True,
                "connect_running": True,
                "connect_logged_in": True,
                "user_session_active": True,
                "eula_accepted": True,
                "license_service_installed": True,
                "has_valid_access_key": False,
                "products": [
                    {
                        "product_id": 1562,
                        "product_name": "STAAD.Pro",
                        "entitlement_count": 2,
                        "status": "Ok",
                    }
                ],
            }

            with patch("app.services.health_checks._run_bentley_license_probe", return_value=payload):
                result = run_license_health_check(settings=settings)

            self.assertTrue(result.healthy)
            self.assertEqual(result.status, "healthy")
            self.assertEqual(result.check, "license")

    def test_license_health_reports_logged_out_state(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            payload = {
                "desktop_api_installed": True,
                "connect_running": True,
                "connect_logged_in": False,
                "user_session_active": True,
                "eula_accepted": True,
                "license_service_installed": True,
                "has_valid_access_key": False,
                "products": [],
            }

            with patch("app.services.health_checks._run_bentley_license_probe", return_value=payload):
                result = run_license_health_check(settings=settings)

            self.assertFalse(result.healthy)
            self.assertEqual(result.status, "unhealthy")
            self.assertIn("not logged in", result.message.lower())

    def test_license_health_reports_missing_entitlement(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            payload = {
                "desktop_api_installed": True,
                "connect_running": True,
                "connect_logged_in": True,
                "user_session_active": True,
                "eula_accepted": True,
                "license_service_installed": True,
                "has_valid_access_key": False,
                "products": [
                    {
                        "product_id": 1562,
                        "product_name": "STAAD.Pro",
                        "entitlement_count": 0,
                        "status": "Expired",
                    }
                ],
            }

            with patch("app.services.health_checks._run_bentley_license_probe", return_value=payload):
                result = run_license_health_check(settings=settings)

            self.assertFalse(result.healthy)
            self.assertEqual(result.status, "unhealthy")
            self.assertIn("entitlement", result.message.lower())

    def test_license_health_reports_probe_timeout(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            with patch(
                "app.services.health_checks._run_bentley_license_probe",
                side_effect=subprocess.TimeoutExpired(cmd="powershell.exe", timeout=15),
            ):
                result = run_license_health_check(settings=settings)

            self.assertFalse(result.healthy)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.details["error_code"], "license_probe_timeout")


class FailureClassificationTests(TestCase):
    def test_non_interactive_session_is_reported_explicitly(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = make_settings(base)

            err = _classify_failure(
                (
                    "STAAD.Pro cannot be automated from a non-interactive Windows session. "
                    "This commonly happens when Windows Task Scheduler is configured with "
                    "'Run whether user is logged on or not'. Run the STAAD-hosting task in the "
                    "logged-in desktop session instead, for example by selecting "
                    "'Run only when user is logged on'."
                ),
                settings,
            )

        self.assertEqual(err.error_code, "non_interactive_session")
        self.assertFalse(err.retryable)
        self.assertEqual(err.stage, "connection")
        self.assertIn("Run the API and STAAD under the logged-in Windows desktop session", err.hint)


class ApiEndpointTests(IsolatedAsyncioTestCase):
    @contextmanager
    def api_context(
        self,
        settings: Settings,
        *,
        runner_side_effect=None,
    ):
        manager = JobManager(settings=settings)
        default_runner = runner_side_effect or (lambda **kwargs: (success_result(), 1))

        with ExitStack() as stack:
            stack.enter_context(patch.object(main_module, "manager", manager))
            stack.enter_context(patch.object(main_module, "settings", settings))
            stack.enter_context(patch("app.job_manager.run_extractor_with_retries", side_effect=default_runner))
            yield manager

    async def post_job(
        self,
        *,
        filename: str = "model.std",
        content: bytes = b"STAAD SPACE",
        timeout_seconds: int = 30,
        max_retries: int = 2,
        retry_delay_seconds: int = 1,
        extraction_flow: str = "standard",
    ):
        upload = UploadFile(filename=filename, file=io.BytesIO(content))
        return await main_module.create_job(
            std_file=upload,
            staadpro_path=None,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            extraction_flow=extraction_flow,
            generation_request=None,
        )

    async def wait_for_terminal_status(self, job_id: str, *, timeout: float = 5.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = (await main_module.get_job_status(job_id)).model_dump(mode="json")
            if payload["status"] in {"succeeded", "failed", "canceled"}:
                return payload
            await asyncio.sleep(0.02)
        self.fail(f"Job {job_id} did not reach a terminal state in time")

    async def get_health_http(self, query_string: str = "") -> tuple[int, str]:
        messages: list[dict[str, object]] = []
        receive_count = 0

        async def receive():
            nonlocal receive_count
            if receive_count == 0:
                receive_count += 1
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/health",
            "raw_path": b"/health",
            "query_string": query_string.encode("utf-8"),
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "root_path": "",
            "app": main_module.app,
        }

        await main_module.app(scope, receive, send)

        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return int(start["status"]), body.decode("utf-8")

    async def test_health_defaults_to_server_readiness(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    status_code, payload = await self.get_health_http()

                    self.assertEqual(status_code, 200)
                    self.assertIn('"check":"server"', payload)
                    self.assertIn('"status":"ok"', payload)
                finally:
                    await manager.shutdown()

    async def test_health_server_check_returns_ready_state(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    status_code, payload = await self.get_health_http("check=server")

                    self.assertEqual(status_code, 200)
                    self.assertIn('"check":"server"', payload)
                    self.assertIn('"status":"ok"', payload)
                finally:
                    await manager.shutdown()

    async def test_health_license_check_returns_200_when_healthy(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    with patch.object(
                        main_module,
                        "run_license_health_check",
                        return_value=main_module.HealthResponse(
                            check="license",
                            status="healthy",
                            healthy=True,
                            message="License check passed.",
                        ),
                    ):
                        response = await main_module.health(check="license")

                    payload = response.body.decode("utf-8")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('"check":"license"', payload)
                    self.assertIn('"status":"healthy"', payload)
                finally:
                    await manager.shutdown()

    async def test_health_license_check_returns_503_when_unhealthy(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    with patch.object(
                        main_module,
                        "run_license_health_check",
                        return_value=main_module.HealthResponse(
                            check="license",
                            status="unhealthy",
                            healthy=False,
                            message="Connection Client is not logged in.",
                            details={"error_code": "license_not_logged_in"},
                        ),
                    ):
                        response = await main_module.health(check="license")

                    self.assertEqual(response.status_code, 503)
                    self.assertIn("not logged in", response.body.decode("utf-8").lower())
                finally:
                    await manager.shutdown()

    async def test_health_staad_check_returns_200_when_healthy(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    with patch.object(
                        main_module,
                        "run_staad_readiness_check",
                        return_value=main_module.HealthResponse(
                            check="staad",
                            status="healthy",
                            healthy=True,
                            message="STAAD check passed.",
                        ),
                    ):
                        response = await main_module.health(check="staad")

                    payload = response.body.decode("utf-8")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('"check":"staad"', payload)
                    self.assertIn('"status":"healthy"', payload)
                finally:
                    await manager.shutdown()

    async def test_health_staad_check_returns_503_when_unhealthy(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    with patch.object(
                        main_module,
                        "run_staad_readiness_check",
                        return_value=main_module.HealthResponse(
                            check="staad",
                            status="unhealthy",
                            healthy=False,
                            message="OpenSTAAD is blocked by a modal dialog.",
                            details={"error_code": "staad_connection_failed"},
                        ),
                    ):
                        response = await main_module.health(check="staad")

                    self.assertEqual(response.status_code, 503)
                    self.assertIn("modal dialog", response.body.decode("utf-8").lower())
                finally:
                    await manager.shutdown()

    async def test_invalid_health_check_returns_422(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    status_code, payload = await self.get_health_http("check=invalid")

                    self.assertEqual(status_code, 422)
                    self.assertIn("literal_error", payload)
                finally:
                    await manager.shutdown()

    async def test_multiple_jobs_submitted_together_complete_successfully(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    first = await self.post_job()
                    second = await self.post_job(filename="second.std")

                    self.assertNotEqual(first.job_id, second.job_id)
                    self.assertEqual((await self.wait_for_terminal_status(first.job_id))["status"], "succeeded")
                    self.assertEqual((await self.wait_for_terminal_status(second.job_id))["status"], "succeeded")
                finally:
                    await manager.shutdown()

    async def test_status_reflects_running_and_queued_jobs(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            release_first = threading.Event()
            first_started = threading.Event()
            call_order: list[str] = []

            def fake_runner(**kwargs):
                call_order.append(kwargs["job_id"])
                kwargs["progress_callback"](45, "Running STAAD analysis.")
                if len(call_order) == 1:
                    first_started.set()
                    self.assertTrue(release_first.wait(timeout=5.0))
                return success_result(), 1

            with self.api_context(settings, runner_side_effect=fake_runner) as manager:
                try:
                    first = await self.post_job()
                    self.assertTrue(first_started.wait(timeout=2.0))

                    second = await self.post_job(filename="second.std")

                    first_status = await main_module.get_job_status(first.job_id)
                    for _ in range(50):
                        if first_status.progress_percent >= 45:
                            break
                        await asyncio.sleep(0.02)
                        first_status = await main_module.get_job_status(first.job_id)
                    second_status = await main_module.get_job_status(second.job_id)

                    self.assertEqual(first_status.status, "running")
                    self.assertEqual(first_status.progress_percent, 45)
                    self.assertEqual(first_status.message, "Running STAAD analysis.")
                    self.assertEqual(second_status.status, "queued")
                    self.assertEqual(second_status.progress_percent, 0)
                    self.assertEqual(second_status.message, "Job is not started yet.")

                    release_first.set()
                    self.assertEqual((await self.wait_for_terminal_status(first.job_id))["status"], "succeeded")
                    self.assertEqual((await self.wait_for_terminal_status(second.job_id))["status"], "succeeded")
                finally:
                    release_first.set()
                    await manager.shutdown()

    async def test_abort_queued_job_skips_extractor(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            release_first = threading.Event()
            first_started = threading.Event()
            call_order: list[str] = []

            def fake_runner(**kwargs):
                call_order.append(kwargs["job_id"])
                kwargs["progress_callback"](45, "Running STAAD analysis.")
                if len(call_order) == 1:
                    first_started.set()
                    self.assertTrue(release_first.wait(timeout=5.0))
                return success_result(), 1

            with self.api_context(settings, runner_side_effect=fake_runner) as manager:
                try:
                    first = await self.post_job()
                    self.assertTrue(first_started.wait(timeout=2.0))
                    second = await self.post_job(filename="second.std")

                    abort_payload = await main_module.abort_job(second.job_id)

                    self.assertEqual(abort_payload.status, "canceled")
                    second_status = await main_module.get_job_status(second.job_id)
                    self.assertEqual(second_status.status, "canceled")
                    self.assertEqual(second_status.progress_percent, 100)
                    self.assertEqual(second_status.message, "Job canceled before execution.")
                    self.assertIsNotNone(second_status.finished_at)
                    self.assertFalse(second_status.result_ready)

                    release_first.set()
                    self.assertEqual((await self.wait_for_terminal_status(first.job_id))["status"], "succeeded")
                    await asyncio.wait_for(manager.queue.join(), timeout=5.0)

                    self.assertEqual(call_order, [first.job_id])
                    self.assertEqual((await self.wait_for_terminal_status(second.job_id))["status"], "canceled")
                finally:
                    release_first.set()
                    await manager.shutdown()

    async def test_abort_running_job_stops_extractor_and_unblocks_queue(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            first_started = threading.Event()
            call_order: list[str] = []

            def fake_runner(**kwargs):
                call_order.append(kwargs["job_id"])
                abort_check = kwargs.get("abort_check")
                kwargs["progress_callback"](45, "Running STAAD analysis.")
                if kwargs.get("pid_callback") is not None:
                    kwargs["pid_callback"](4242)
                if len(call_order) == 1:
                    first_started.set()
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        if abort_check is not None and abort_check():
                            if kwargs.get("pid_callback") is not None:
                                kwargs["pid_callback"](None)
                            raise ExtractorExecutionError(
                                message="STAAD job was aborted by client request.",
                                retryable=False,
                                error_code="canceled",
                                attempts=1,
                                stage="execution",
                            )
                        time.sleep(0.02)
                    self.fail("Running job was not aborted in time")
                if kwargs.get("pid_callback") is not None:
                    kwargs["pid_callback"](None)
                return success_result(), 1

            with (
                self.api_context(settings, runner_side_effect=fake_runner) as manager,
                patch.object(manager, "_force_stop_running_job_processes") as kill_mock,
            ):
                try:
                    first = await self.post_job(filename="first.std")
                    self.assertTrue(first_started.wait(timeout=2.0))
                    second = await self.post_job(filename="second.std")

                    abort_payload = await main_module.abort_job(first.job_id)

                    self.assertEqual(abort_payload.status, "canceled")
                    self.assertEqual(abort_payload.message, "Job canceled during execution.")
                    kill_mock.assert_called()
                    first_status = await main_module.get_job_status(first.job_id)
                    self.assertEqual(first_status.status, "canceled")
                    self.assertEqual(first_status.progress_percent, 100)

                    self.assertEqual((await self.wait_for_terminal_status(second.job_id))["status"], "succeeded")
                    self.assertEqual(call_order, [first.job_id, second.job_id])
                finally:
                    await manager.shutdown()

    async def test_result_endpoint_returns_canceled_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            release_first = threading.Event()
            first_started = threading.Event()

            def fake_runner(**kwargs):
                first_started.set()
                self.assertTrue(release_first.wait(timeout=5.0))
                return success_result(), 1

            with self.api_context(settings, runner_side_effect=fake_runner) as manager:
                try:
                    first = await self.post_job()
                    self.assertTrue(first_started.wait(timeout=2.0))
                    second = await self.post_job(filename="second.std")

                    await main_module.abort_job(second.job_id)
                    payload = await main_module.get_job_result(second.job_id)

                    self.assertEqual(payload["status"], "canceled")
                    self.assertEqual(payload["message"], "Job canceled before execution.")
                    self.assertTrue(payload["cleanup_performed"])

                    release_first.set()
                    self.assertEqual((await self.wait_for_terminal_status(first.job_id))["status"], "succeeded")
                finally:
                    release_first.set()
                    await manager.shutdown()

    async def test_abort_missing_job_returns_404(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    with self.assertRaises(HTTPException) as exc_info:
                        await main_module.abort_job("missing-job")

                    self.assertEqual(exc_info.exception.status_code, 404)
                    self.assertEqual(exc_info.exception.detail["error_code"], "job_not_found")
                finally:
                    await manager.shutdown()

    async def test_result_endpoint_returns_success_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            with self.api_context(settings) as manager:
                try:
                    created = await self.post_job(max_retries=0)

                    status_payload = await self.wait_for_terminal_status(created.job_id)
                    self.assertEqual(status_payload["status"], "succeeded")
                    self.assertEqual(status_payload["progress_percent"], 100)
                    self.assertEqual(status_payload["message"], "Job completed successfully.")
                    self.assertIsNotNone(status_payload["started_at"])
                    self.assertIsNotNone(status_payload["finished_at"])

                    payload = await main_module.get_job_result(created.job_id)

                    self.assertEqual(payload["status"], "succeeded")
                    self.assertEqual(payload["result"], success_result())
                    self.assertEqual(payload["attempts"], 1)
                    self.assertEqual(payload["max_attempts"], 1)
                finally:
                    await manager.shutdown()

    async def test_failed_job_status_and_result_are_sanitized(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            def failing_runner(**kwargs):
                raise ExtractorExecutionError(
                    message="License dialog blocked execution.",
                    retryable=False,
                    stderr_excerpt="traceback data that must not leak",
                    error_code="staad_connection_failed",
                    attempts=1,
                    stage="connection",
                    hint="Dismiss STAAD dialogs in the logged-in desktop session.",
                    details={"traceback_excerpt": "do not expose", "exception_type": "RuntimeError"},
                )

            with self.api_context(settings, runner_side_effect=failing_runner) as manager:
                try:
                    created = await self.post_job(max_retries=0)

                    status_payload = await self.wait_for_terminal_status(created.job_id)
                    self.assertEqual(status_payload["status"], "failed")
                    self.assertEqual(status_payload["progress_percent"], 100)
                    self.assertEqual(status_payload["message"], "Job failed.")
                    self.assertEqual(status_payload["error"]["error_code"], "staad_connection_failed")
                    self.assertIsNone(status_payload["error"]["last_stderr_excerpt"])
                    self.assertEqual(
                        status_payload["error"]["details"],
                        {
                            "error_code": "staad_connection_failed",
                            "retryable": False,
                            "attempts": 1,
                            "stage": "connection",
                            "hint": "Dismiss STAAD dialogs in the logged-in desktop session.",
                        },
                    )

                    with self.assertRaises(HTTPException) as exc_info:
                        await main_module.get_job_result(created.job_id)
                    result_payload = exc_info.exception.detail
                    self.assertEqual(result_payload["error_code"], "staad_connection_failed")
                    self.assertNotIn("last_stderr_excerpt", result_payload)
                    self.assertEqual(
                        result_payload["details"],
                        {
                            "error_code": "staad_connection_failed",
                            "retryable": False,
                            "attempts": 1,
                            "stage": "connection",
                            "hint": "Dismiss STAAD dialogs in the logged-in desktop session.",
                        },
                    )
                finally:
                    await manager.shutdown()

    async def test_timeout_failure_is_reported_safely(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            def timeout_runner(**kwargs):
                raise ExtractorExecutionError(
                    message="STAAD execution timed out after 300 seconds.",
                    retryable=True,
                    error_code="timeout",
                    attempts=2,
                    stage="execution",
                    hint="Increase timeout_seconds or clear blocking dialogs.",
                    details={"traceback_excerpt": "internal"},
                )

            with self.api_context(settings, runner_side_effect=timeout_runner) as manager:
                try:
                    created = await self.post_job(max_retries=1)

                    status_payload = await self.wait_for_terminal_status(created.job_id)
                    self.assertEqual(status_payload["status"], "failed")
                    self.assertEqual(status_payload["attempts"], 2)
                    self.assertEqual(status_payload["error"]["error_code"], "timeout")

                    with self.assertRaises(HTTPException) as exc_info:
                        await main_module.get_job_result(created.job_id)
                    result_payload = exc_info.exception.detail
                    self.assertEqual(result_payload["error_code"], "timeout")
                    self.assertEqual(result_payload["attempts"], 2)
                    self.assertNotIn("last_stderr_excerpt", result_payload)
                finally:
                    await manager.shutdown()

    async def test_retry_success_preserves_attempt_count(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            def retry_then_success(**kwargs):
                return success_result(), 2

            with self.api_context(settings, runner_side_effect=retry_then_success) as manager:
                try:
                    created = await self.post_job(max_retries=2)

                    status_payload = await self.wait_for_terminal_status(created.job_id)
                    self.assertEqual(status_payload["status"], "succeeded")
                    self.assertEqual(status_payload["attempts"], 2)
                    self.assertEqual(status_payload["max_attempts"], 3)

                    result_payload = await main_module.get_job_result(created.job_id)
                    self.assertEqual(result_payload["attempts"], 2)
                    self.assertEqual(result_payload["max_attempts"], 3)
                finally:
                    await manager.shutdown()

    async def test_retry_exhausted_returns_failed_job(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            def retry_exhausted(**kwargs):
                raise ExtractorExecutionError(
                    message="STAAD execution timed out after retries.",
                    retryable=True,
                    error_code="timeout",
                    attempts=3,
                    stage="execution",
                    hint="Increase timeout_seconds or inspect blocking dialogs.",
                    details={"traceback_excerpt": "internal"},
                )

            with self.api_context(settings, runner_side_effect=retry_exhausted) as manager:
                try:
                    created = await self.post_job(max_retries=2)

                    status_payload = await self.wait_for_terminal_status(created.job_id)
                    self.assertEqual(status_payload["status"], "failed")
                    self.assertEqual(status_payload["attempts"], 3)
                    self.assertEqual(status_payload["max_attempts"], 3)

                    with self.assertRaises(HTTPException) as exc_info:
                        await main_module.get_job_result(created.job_id)
                    result_payload = exc_info.exception.detail
                    self.assertEqual(result_payload["error_code"], "timeout")
                    self.assertEqual(result_payload["attempts"], 3)
                finally:
                    await manager.shutdown()

    async def test_post_job_does_not_invoke_preflight(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = make_settings(base)

            with self.api_context(settings) as manager:
                try:
                    with patch(
                        "app.services.staad_runner.run_staad_preflight",
                        side_effect=AssertionError("POST /jobs should not call STAAD preflight"),
                    ):
                        created = await self.post_job()

                    self.assertEqual(created.status, "queued")
                    status_payload = await self.wait_for_terminal_status(created.job_id)
                    self.assertEqual(status_payload["status"], "succeeded")
                    self.assertTrue((base / "jobs").exists())
                finally:
                    await manager.shutdown()

    async def test_casement_extraction_flow_is_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = make_settings(base)

            with self.api_context(settings) as manager:
                try:
                    created = await self.post_job(extraction_flow="casement")
                    self.assertEqual(created.status, "queued")
                    status_payload = await self.wait_for_terminal_status(created.job_id)
                    self.assertEqual(status_payload["status"], "succeeded")
                finally:
                    await manager.shutdown()

    async def test_invalid_extraction_flow_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = make_settings(base)

            with self.api_context(settings) as manager:
                try:
                    with self.assertRaises(HTTPException) as raised:
                        await self.post_job(extraction_flow="not_a_real_flow")
                    self.assertEqual(raised.exception.status_code, 422)
                    detail = raised.exception.detail
                    self.assertEqual(detail["error_code"], "invalid_extraction_flow")
                finally:
                    await manager.shutdown()
