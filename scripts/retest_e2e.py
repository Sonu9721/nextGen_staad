"""Run real HTTP workflows on an isolated, owned local server and save evidence."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import __version__


def main():
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    folder = ROOT / "runtime" / "e2e"
    folder.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        ANALYSIS_BACKEND="inhouse",
        STAAD_JOBS_DIR=str(folder / "jobs"),
        TEMP_DIR=str(folder / "temp"),
        LOGS_DIR=str(folder / "logs"),
        JOB_RETENTION_HOURS="0",
        STAAD_API_MAX_UPLOAD_BYTES="262144",
    )
    report = {
        "solver_version": __version__,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "samples": {},
    }

    def passed(name, **details):
        report["checks"].append(dict(name=name, status="pass", **details))
        print("PASS", name, flush=True)

    log = (folder / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
            deadline = time.monotonic() + 20
            while True:
                try:
                    if client.get("/health").is_success:
                        break
                except httpx.TransportError:
                    pass
                assert process.poll() is None, "Server exited during startup"
                assert time.monotonic() < deadline, "Server startup timed out"
                time.sleep(0.1)
            for path in [
                "/",
                "/console.js",
                "/console.css",
                "/docs",
                "/openapi.json",
                "/validation-report",
                "/usg-validation-report",
            ]:
                response = client.get(path)
                assert response.status_code == 200, (path, response.text)
                response.content.decode("utf-8", errors="strict")
            passed("Server, interface assets, documentation and UTF-8 encoding")
            for kind in ["server", "license", "staad"]:
                response = client.get("/health", params={"check": kind})
                assert response.status_code == 200 and response.json()["healthy"]
            passed("All three health endpoints without STAAD license")
            for path in [
                "/jobs/missing",
                "/jobs/missing/result",
                "/jobs/missing/model",
                "/examples/missing",
            ]:
                assert client.get(path).status_code == 404
            assert client.post("/jobs/missing/abort").status_code == 404
            passed("Unknown resources return 404")

            def submit(content, filename="model.std", flow="standard", request=None):
                files = {"std_file": (filename, content, "text/plain")}
                if request is not None:
                    files["generation_request"] = (
                        "request.json",
                        request,
                        "application/json",
                    )
                response = client.post(
                    "/jobs", files=files, data={"extraction_flow": flow}
                )
                assert response.status_code == 202, response.text
                return response.json()["job_id"]

            def wait(jid, wanted=None):
                deadline = time.monotonic() + 180
                previous = 0
                while time.monotonic() < deadline:
                    state = client.get(f"/jobs/{jid}").json()
                    assert state["progress_percent"] >= previous, state
                    previous = state["progress_percent"]
                    if state["status"] in {"succeeded", "failed", "canceled"}:
                        if wanted:
                            assert state["status"] == wanted, state
                        return state
                    time.sleep(0.1)
                raise AssertionError("Job deadline exceeded")

            for name, flow in [
                ("sample1", "fully_unitized"),
                ("sample2", "fully_unitized"),
                ("sample3", "fully_unitized"),
                ("sample4", "casement"),
                ("sample5", "casement"),
                ("sample6", "casement"),
                ("sample7", "casement"),
                ("usg1", "fully_unitized"),
                ("usg2", "fully_unitized"),
            ]:
                content = client.get(f"/examples/{name}").content
                jid = submit(content, name + ".std", flow)
                pending = client.get(f"/jobs/{jid}/result").json()
                assert pending["status"] in (
                    {"queued", "running", "failed"}
                    if name == "sample7"
                    else {"queued", "running", "succeeded"}
                )
                if name == "sample7":
                    state = wait(jid, "failed")
                    assert state["attempts"] == 1 and not state["error"]["retryable"]
                    assert state["error"]["error_code"] == "SINGULAR_MATRIX"
                    assert "UY at nodes" in state["error"]["message"]
                    assert client.get(f"/jobs/{jid}/result").status_code == 500
                    geometry = client.get(f"/jobs/{jid}/model").json()
                    assert (
                        len(geometry["nodes"]) == 48 and len(geometry["members"]) == 68
                    )
                    report["samples"][name] = dict(
                        analysis_status="rejected",
                        reason=state["error"]["message"],
                        nodes=48,
                        members=68,
                    )
                    passed(
                        "sample7: imported with CMOM; loaded mechanism rejected with actionable diagnostic"
                    )
                    continue
                state = wait(jid, "succeeded")
                assert state["progress_percent"] == 100 and state["attempts"] == 1
                wrapper = client.get(f"/jobs/{jid}/result").json()
                data = wrapper["result"]
                json.dumps(data, allow_nan=False)
                assert data["analysis"]["solver_version"] == __version__
                assert data["analysis"]["load_cases"] == (
                    [1, 2, 3, 4, 5, 6] if name == "sample7" else [1, 2, 3, 4]
                )
                assert (
                    data["physical_response"]["load_cases"]
                    == data["analysis"]["load_cases"]
                )
                for balance in data["analysis"]["diagnostics"]["equilibrium"].values():
                    assert balance["relative_force_error"] < 1e-9
                    assert balance["relative_moment_error"] < 1e-9
                geometry = client.get(f"/jobs/{jid}/model").json()
                assert (
                    len(geometry["members"])
                    == data["analysis"]["diagnostics"]["members"]
                )
                if flow == "casement":
                    assert len(data["casement"]["profiles"]) == 5
                report["samples"][name] = dict(
                    duration_seconds=wrapper["duration_seconds"],
                    nodes=len(geometry["nodes"]),
                    members=len(geometry["members"]),
                    physical_response=data["physical_response"],
                )
                if name == "sample4":
                    repeat_source, repeat_expected = content, data
                passed(
                    f"{name}: upload, progress, numerical analysis, geometry and result export"
                )
            jid = submit(repeat_source, "../repeat.std", "casement", b"{}")
            wait(jid, "succeeded")
            repeated = client.get(f"/jobs/{jid}/result").json()["result"]
            for key in ["properties", "casement", "physical_response"]:
                assert repeated[key] == repeat_expected[key], key
            assert Path(repeated["file"]).name == "repeat.std"
            passed(
                "Repeat analysis is deterministic; profile JSON and upload basename are handled"
            )
            for content, request, code in [
                (
                    b"STAAD SPACE\nUNIT METER KN\nPDELTA ANALYSIS\nFINISH",
                    None,
                    "UNSUPPORTED_STAAD_COMMAND",
                ),
                (repeat_source, b"[]", "INVALID_STD_FILE"),
                (repeat_source, b"{", "INVALID_STD_FILE"),
            ]:
                jid = submit(content, request=request)
                state = wait(jid, "failed")
                assert (
                    state["error"]["error_code"] == code
                    and not state["error"]["retryable"]
                    and state["attempts"] == 1
                )
                assert client.get(f"/jobs/{jid}/result").status_code == 500
            passed(
                "Unsupported analysis and malformed profile requests fail without retries"
            )
            for files, data, status in [
                ({"std_file": ("bad.txt", b"bad")}, {}, 422),
                ({"std_file": ("big.std", b"x" * 262145)}, {}, 413),
                (
                    {"std_file": ("a.std", repeat_source)},
                    {"extraction_flow": "invalid"},
                    422,
                ),
                ({"std_file": ("a.std", repeat_source)}, {"timeout_seconds": "0"}, 422),
            ]:
                assert (
                    client.post("/jobs", files=files, data=data).status_code == status
                )
            passed("Wrong type, upload limit, flow and timeout validation")
            first = submit(client.get("/examples/sample2").content)
            second = submit(repeat_source)
            assert client.post(f"/jobs/{second}/abort").status_code == 200
            wait(second, "canceled")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = client.get(f"/jobs/{first}").json()
                if state["progress_percent"] >= 30:
                    break
                time.sleep(0.05)
            assert state["status"] == "running"
            assert client.post(f"/jobs/{first}/abort").status_code == 200
            wait(first, "canceled")
            assert client.get(f"/jobs/{first}/result").json()["status"] == "canceled"
            passed("Queued and actively solving jobs cancel; server remains responsive")
            assert client.get("/health").json()["healthy"]
        report["status"] = "pass"
    except Exception as exc:
        report["status"] = "fail"
        report["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (ROOT / "validation/e2e.json").write_text(
            json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
