**# STAAD Report Extractor API

FastAPI service for uploading a `.std` model, queueing the request, running STAAD
through a dedicated background worker, and polling for the extracted maximum result set.

Runtime architecture:

`Client -> API -> Queue -> Worker -> STAAD -> Result`

## Current API

- `GET /health`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/result`
- `POST /jobs/{job_id}/abort`

`GET /health` supports a `check` query selector:

- `GET /health` or `GET /health?check=server`: FastAPI/process readiness
- `GET /health?check=license`: fast Bentley Connection Client and licensing readiness
- `GET /health?check=staad`: deeper STAAD/OpenSTAAD readiness

`POST /jobs` accepts:

- `std_file`: required `.std` upload
- `timeout_seconds`: optional, default `180`
- `max_retries`: optional, default `2`
- `retry_delay_seconds`: optional, default `20`
- `staadpro_path`: optional legacy field; accepted for backward compatibility but not required by the new extractor
- `extraction_flow`: optional, defaults to `standard`. Use `fully_unitized` for Fully Unitized STAAD profile-specific extraction, or `casement` for Casement signed profile envelopes. Aliases `unitized` / `fullyunitized` map to `fully_unitized`; hyphenated `casement` maps to `casement`
- `generation_request`: optional JSON file upload. For Fully Unitized it supplies panel geometry and an optional flow override. For Casement it may set `extraction_flow` / `flowType` and an optional `casement_profiles` member-id map (camelCase `casementProfiles` is accepted)

## Result Payload

The succeeded `/jobs/{job_id}/result` response returns a single grouped `result` object. Engineering values are organized under `result.properties`.

### Standard flow (`extraction_flow=standard`, default)

- `properties.bending_moment.major`: maximum major-direction `Mz`
- `properties.bending_moment.minor.max`: maximum minor-direction `My`
- `properties.bending_moment.minor.at_major_governing_point`: `My` at the governing `Mz` point
- `properties.shear_force.major`: maximum major-direction `Fy`
- `properties.shear_force.minor.max_on_mullions`: maximum minor-direction `Fz` from vertical Mullion members only
- `properties.shear_force.minor.at_major_governing_point`: maximum `Fz` across selected load cases at the fixed point where major `Fy` governs
- `properties.axial_force`: maximum axial `FX` from **inner vertical members only** (peripheral/outer frame verticals are excluded)
- `properties.displacement`: grouped displacement results

### Fully unitized flow (`extraction_flow=fully_unitized`)

The standard top-level `properties.{bending_moment,shear_force,axial_force,displacement}` block remains the **global** envelope across all members. Additional keys are included:

- `properties.global`: mirror of the top-level global envelope
- `properties.mullion`, `properties.head`, `properties.sill`, `properties.transom`, `properties.stack`: per-profile envelopes (omitted when a group has no members)
- `profiles.extraction_flow`: `fully_unitized`
- `profiles.member_counts`: member counts per classified profile group

Each profile block contains the same grouped quantity structure as the global envelope. Mullion groups include minor-axis bending (`My`) and minor shear (`Fz`); horizontal profile groups include major-axis values only.

### Casement flow (`extraction_flow=casement`)

Sliding (`standard`) and Fully Unitized payloads stay structurally unchanged. Casement keeps the same **standard-shaped** global `properties` envelope (absolute governing magnitudes) and appends a separate top-level `casement` object:

- `casement.extraction_flow`: `casement`
- `casement.profiles`: always includes all five keys — `interlock`, `central_meeting`, `fixed_mullion`, `horizontal`, `outer`

Each profile contains `member_ids` plus `bm` / `sf` / `af` / `df`. Those metrics are **signed** envelopes (`max` = most positive algebraic value, `min` = most negative), not absolute magnitudes. `max` or `min` is `null` when that profile has no samples.

| Metric | Axis | Unit | Source |
| --- | --- | --- | --- |
| `bm` | `MZ` | `kN-m` | Member internal moment envelope |
| `sf` | `FY` | `kN` | Member end forces |
| `af` | `FX` | `kN` | Member end forces |
| `df` | `RESULTANT` | `mm` | Member-chord intermediate deflection, plus exclusive nodal RESULTANT. Horizontal also includes absolute nodal RESULTANT at shared junctions |

Member classification (disjoint; first listed profile wins on duplicates):

1. Optional `generation_request.casement_profiles` / `casementProfiles` member-id map, if present with at least one valid id. Unknown ids are dropped.
2. Otherwise geometry: outer frame at bounding min/max X and Y; remaining interior horizontals → `horizontal`; interior verticals below the transom line → `fixed_mullion`; above-transom median X-column → `central_meeting`; remaining above-transom verticals → `interlock`.

If Casement extraction fails after analysis, the job still succeeds with an empty `casement` object (all five profiles present, `member_ids` empty, `max`/`min` null). The `casement` object is **not** present for `standard` or `fully_unitized` responses.

The flow can also be forced from `generation_request` via `flowType`, `flow_type`, `extractionFlow`, or `extraction_flow`.

Analysis settings are grouped under `result.analysis`, and units are grouped under `result.units`. Legacy scattered keys such as `max_bending_moment`, `max_shear_force`, and `max_sf_major_fy` are not emitted in the normal payload.

## Job Progress Polling

Caller applications should poll `GET /jobs/{job_id}` to drive a UI progress bar. The status response includes:

- `status`: job lifecycle state: `queued`, `running`, `succeeded`, `failed`, or `canceled`
- `progress_percent`: integer from `0` to `100`
- `message`: user-facing status text that can be shown next to the progress bar
- `result_ready`: `true` only when the result endpoint can return the extracted payload

When a job has been accepted but the worker has not started it yet, the API returns:

```json
{
  "status": "queued",
  "progress_percent": 0,
  "message": "Job is not started yet.",
  "result_ready": false
}
```

While running, the percent advances through stable extraction stages such as preparing input, connecting to STAAD, running analysis, extracting results, and finalizing. Non-terminal jobs are capped below `100`; terminal jobs always return `100` whether they succeeded, failed, or were canceled.

Recommended caller behavior:

- Start polling `GET /jobs/{job_id}` after `POST /jobs` returns `202`.
- Update the UI progress bar from `progress_percent` and display `message` as the status label.
- Continue polling while `status` is `queued` or `running`.
- Stop polling when `status` is `succeeded`, `failed`, or `canceled`.
- Call `GET /jobs/{job_id}/result` only when `result_ready` is `true`.

## Production Design

- `POST /jobs` only stages the upload and enqueues work; the API request path does not launch or attach to STAAD.
- `POST /jobs/{job_id}/abort` cancels queued jobs immediately, or force-stops a running STAAD job (kills the extractor process tree and related STAAD processes) so the queue can advance.
- Java or another caller can decide when to invoke `/health?check=license` and `/health?check=staad`; `POST /jobs` does not run preflight automatically.
- By default, a single in-process worker consumes the queue and executes one STAAD job at a time.
- `STAAD_WORKER_COUNT` can increase in-process worker count only when `ALLOW_PARALLEL_STAAD=true`.
- The worker also takes a host-level STAAD lock so even accidental multi-process deployments do not drive STAAD in parallel.
- Completed-job pruning runs in a lightweight background maintenance loop instead of on every submission.
- Each attempt runs inside a managed child process with explicit startup, COM attach, analysis, and outer job timeouts.
- Before and after each attempt, the worker cleans STAAD processes, releases COM references, runs `gc.collect()`, and trims generated job artifacts.
- Completed job metadata is pruned from memory after a TTL to keep the server footprint bounded.
- Status polling returns `progress_percent` and `message` so caller applications can show progress while a job is queued or running.
- Result polling returns a single `result` payload field.
- Submission logs include staging and total queue-accept latency so the API hot path can be measured separately from STAAD runtime.

## Production Notes

- This service is intended for Windows hosts with STAAD.Pro already installed.
- Default runtime folders are `C:\Temp`, `C:\STAADJobs`, and `C:\Logs`.
- Keep `ALLOW_PARALLEL_STAAD=false`. Stable production use assumes only one STAAD job runs at a time.
- Keep `STAAD_WORKER_COUNT=1` unless STAAD/OpenSTAAD concurrency has been explicitly validated in that environment.
- `pywin32` is required because the extractor uses OpenSTAAD COM automation.
- `comtypes` is required for Bentley's bundled `openstaadpy` wrapper.
- For faster repeated jobs, `KEEP_STAAD_WARM=true` keeps a successfully attached STAAD session running between jobs. If a warm instance becomes unattachable, the next launch cleans up the stale STAAD process first so parallel STAAD instances do not accumulate. If STAAD gets stuck or starts returning stale COM behavior, set it back to `false` and restart the API.
- Run a single Uvicorn worker process for this service. STAAD itself is serialized by the queue/lock design.
- If you launch the API with Windows Task Scheduler, use the same Windows user that can open STAAD manually and set the task to `Run only when user is logged on`. STAAD/OpenSTAAD is a desktop application and usually cannot be launched or automated from the non-interactive scheduler mode `Run whether user is logged on or not`.
- STAAD must be able to launch in the logged-in desktop session. If Bentley sign-in or licensing prompts appear, open STAAD manually once in that same session and clear them before relying on unattended jobs.

Useful environment variables:

- `TEMP_DIR`
- `STAAD_JOBS_DIR`
- `LOGS_DIR`
- `STAAD_TIMEOUT_SECONDS`
- `STAAD_WORKER_COUNT`
- `ALLOW_PARALLEL_STAAD`
- `STAAD_LAUNCH_WAIT_SECONDS`
- `STAAD_STARTUP_TIMEOUT_SECONDS`
- `STAAD_ATTACH_TIMEOUT_SECONDS`
- `STAAD_LOCK_TIMEOUT_SECONDS`
- `KEEP_STAAD_WARM`
- `STAAD_API_MAX_RETRIES`
- `STAAD_API_RETRY_DELAY_SECONDS`
- `COMPLETED_JOB_TTL_SECONDS`
- `CLEANUP_RETRY_SECONDS`
- `CLEANUP_RETRY_INTERVAL_SECONDS`
- `ENABLE_STAAD_PREFLIGHT` (legacy; no longer used by `POST /jobs`)
- `STAAD_PREFLIGHT_TIMEOUT_SECONDS` (used by explicit `GET /health?check=staad`)
- `JOB_RETENTION_HOURS`
- `KEEP_JOB_FOLDER_ON_ERROR`
- `KEEP_ANL_FILE`

## Local Run

```powershell
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

For production on Windows EC2, prefer one API process:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

## Local Test With curl

Health check:

```powershell
curl.exe http://127.0.0.1:8000/health
```

License readiness:

```powershell
curl.exe "http://127.0.0.1:8000/health?check=license"
```

STAAD/OpenSTAAD readiness:

```powershell
curl.exe "http://127.0.0.1:8000/health?check=staad"
```

Create a job:

```powershell
curl.exe -X POST http://127.0.0.1:8000/jobs `
  -F "std_file=@C:\path\to\your\model.std" `
  -F "timeout_seconds=300" `
  -F "max_retries=1" `
  -F "retry_delay_seconds=10"
```

Create a Casement job:

```powershell
curl.exe -X POST http://127.0.0.1:8000/jobs `
  -F "std_file=@C:\path\to\casement.std" `
  -F "extraction_flow=casement" `
  -F "timeout_seconds=300"
```

Optional Casement `generation_request` JSON (saved as a file and uploaded with `-F "generation_request=@casement-request.json"`):

```json
{
  "extraction_flow": "casement",
  "casement_profiles": {
    "interlock": [22, 26],
    "central_meeting": [24],
    "fixed_mullion": [21, 23, 25],
    "horizontal": [47, 48, 49, 50],
    "outer": [1, 2, 3]
  }
}
```

If `casement_profiles` is omitted, members are classified from model geometry.

Backward-compatible create request with `staadpro_path` still present:

```powershell
curl.exe -X POST http://127.0.0.1:8000/jobs `
  -F "std_file=@C:\path\to\your\model.std" `
  -F "staadpro_path=C:\Program Files\Bentley\Engineering\STAAD.Pro 2025\STAAD\Bentley.Staad.exe"
```

Check status:

```powershell
curl.exe http://127.0.0.1:8000/jobs/<job_id>
```

Example status response for a running job:

```json
{
  "job_id": "8d978f35-9f3e-46ee-9378-6e51e65eb9e1",
  "status": "running",
  "progress_percent": 45,
  "message": "Running STAAD analysis.",
  "attempts": 0,
  "max_attempts": 2,
  "created_at": "2026-05-03T15:14:43.123456Z",
  "started_at": "2026-05-03T15:14:44.123456Z",
  "finished_at": null,
  "result_ready": false,
  "error": null,
  "duration_seconds": null,
  "working_directory": "C:\\STAADJobs\\job_8d978f35-9f3e-46ee-9378-6e51e65eb9e1",
  "cleanup_performed": false
}
```

Minimal UI polling loop:

```javascript
async function pollJob(jobId) {
  while (true) {
    const response = await fetch(`/jobs/${jobId}`);
    const job = await response.json();

    progressBar.value = job.progress_percent;
    statusLabel.textContent = job.message;

    if (job.status === "succeeded") {
      const result = await fetch(`/jobs/${jobId}/result`).then((r) => r.json());
      renderResult(result);
      return;
    }

    if (job.status === "failed" || job.status === "canceled") {
      showJobStopped(job.message, job.error);
      return;
    }

    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}
```

Fetch result:

```powershell
curl.exe http://127.0.0.1:8000/jobs/<job_id>/result
```

Abort a queued or running job:

```powershell
curl.exe -X POST http://127.0.0.1:8000/jobs/<job_id>/abort
```
