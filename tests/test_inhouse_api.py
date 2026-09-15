import threading
import time
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.job_manager import JobManager
from app.services.inhouse_runner import run_inhouse
from app.services.staad_runner import ExtractorExecutionError
import app.main as main

ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "staad-max-extractor/tests/fixtures/Casement_CF-3T4S-3F-C_STAAD.std"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        ANALYSIS_BACKEND="inhouse",
        TEMP_DIR=tmp_path / "temp",
        STAAD_JOBS_DIR=tmp_path / "jobs",
        LOGS_DIR=tmp_path / "logs",
        JOB_RETENTION_HOURS=0,
    )


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setattr(main, "manager", JobManager(settings=settings))
    with TestClient(main.app) as client:
        yield client


def wait_job(client, jid, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get("/jobs/" + jid).json()
        if data["status"] in {"succeeded", "failed", "canceled"}:
            return data
        time.sleep(0.05)
    raise AssertionError("Job did not finish")


def test_real_upload_result_and_no_bentley_process_operations(client):
    with patch(
        "app.services.staad_runner.kill_staad_processes_by_name",
        side_effect=AssertionError("Bentley process touched"),
    ):
        response = client.post(
            "/jobs",
            files={"std_file": ("../escape.std", SAMPLE.read_bytes(), "text/plain")},
            data={"extraction_flow": "casement", "staadpro_path": "not-installed.exe"},
        )
        assert response.status_code == 202
        jid = response.json()["job_id"]
        state = wait_job(client, jid)
        assert state["status"] == "succeeded", state
        assert state["progress_percent"] == 100
        data = client.get(f"/jobs/{jid}/result").json()
        assert (
            data["attempts"] == 1 and data["result"]["analysis"]["backend"] == "inhouse"
        )
        assert data["result"]["units"]["output"]["length"] == "mm"
        assert set(data["result"]["casement"]["profiles"]) == {
            "interlock",
            "central_meeting",
            "fixed_mullion",
            "horizontal",
            "outer",
        }
        assert Path(data["result"]["file"]).parent.name == "job_" + jid
        assert Path(data["result"]["file"]).name == "escape.std"


def test_invalid_analysis_returns_structured_nonretryable_error(client):
    response = client.post(
        "/jobs",
        files={
            "std_file": (
                "bad.std",
                b"STAAD SPACE\nUNIT METER KN\nPDELTA ANALYSIS\nFINISH",
            )
        },
    )
    jid = response.json()["job_id"]
    state = wait_job(client, jid)
    assert state["status"] == "failed"
    assert state["error"]["error_code"] == "UNSUPPORTED_STAAD_COMMAND"
    assert state["error"]["retryable"] is False
    assert state["attempts"] == 1
    assert client.get(f"/jobs/{jid}/result").status_code == 500


def test_health_has_no_license_dependency(client):
    for check in ("server", "license", "staad"):
        response = client.get("/health", params={"check": check})
        assert response.status_code == 200 and response.json()["healthy"]


def test_unstable_sample7_keeps_geometry_but_never_returns_analysis_results(client):
    path = ROOT / "examples/sample7/sample_7.std"
    response = client.post(
        "/jobs",
        files={"std_file": (path.name, path.read_bytes())},
        data={"extraction_flow": "casement"},
    )
    jid = response.json()["job_id"]
    state = wait_job(client, jid)
    assert (
        state["status"] == "failed"
        and state["error"]["error_code"] == "SINGULAR_MATRIX"
    )
    assert "UY at nodes" in state["error"]["message"]
    assert client.get(f"/jobs/{jid}/result").status_code == 500
    geometry = client.get(f"/jobs/{jid}/model").json()
    assert len(geometry["nodes"]) == 48 and len(geometry["members"]) == 68
    assert geometry["supports"]["1"] == [0, 2]


def test_timeout_terminates_only_owned_worker(settings):
    with pytest.raises(ExtractorExecutionError) as exc:
        run_inhouse(settings=settings, std_file_path=SAMPLE, timeout_seconds=0.001)
    assert exc.value.error_code == "timeout"


def test_approved_sample7_example_runs_without_replacing_original(client):
    original = ROOT / "examples/sample7/sample_7.std"
    revised = ROOT / "examples/revisions/sample7/sample_7_axial_connected.std"
    assert client.get("/examples/sample7").content == original.read_bytes()
    response = client.get("/examples/sample7-revised")
    assert response.status_code == 200 and response.content == revised.read_bytes()
    submitted = client.post("/jobs", files={"std_file": (revised.name, response.content)},
                            data={"extraction_flow": "casement"})
    jid = submitted.json()["job_id"]
    assert wait_job(client, jid, timeout=120)["status"] == "succeeded"
    result = client.get(f"/jobs/{jid}/result").json()["result"]
    assert result["analysis"]["load_cases"] == [1, 2, 3, 4, 5, 6]
    assert result["physical_response"]["displacement"]["absolute"]["value"] > 0
    geometry = client.get(f"/jobs/{jid}/model").json()
    assert len(geometry["nodes"]) == 48 and len(geometry["members"]) == 68
    assert geometry["supports"]["1"] == [0, 2]
    assert geometry["supports"]["7"] == [0, 1, 2]


def test_running_abort_returns_without_touching_staad(client):
    with patch(
        "app.job_manager.kill_staad_processes_by_name",
        side_effect=AssertionError("Bentley process touched"),
    ):
        response = client.post(
            "/jobs",
            files={"std_file": ("model.std", SAMPLE.read_bytes())},
            data={"extraction_flow": "casement"},
        )
        jid = response.json()["job_id"]
        aborted = client.post(f"/jobs/{jid}/abort")
        assert aborted.status_code == 200
        assert wait_job(client, jid)["status"] == "canceled"
        assert client.get(f"/jobs/{jid}/result").json()["status"] == "canceled"


def test_cancel_at_assembly_stage(settings):
    event = threading.Event()
    with pytest.raises(ExtractorExecutionError) as exc:
        run_inhouse(
            settings=settings,
            std_file_path=SAMPLE,
            timeout_seconds=20,
            abort_check=event.is_set,
            progress_callback=lambda p, m: event.set() if p >= 30 else None,
        )
    assert exc.value.error_code == "canceled"


def test_backend_validation():
    with pytest.raises(ValueError):
        Settings(ANALYSIS_BACKEND="unknown")
    assert Settings(ANALYSIS_BACKEND="openstaad").analysis_backend == "openstaad"
