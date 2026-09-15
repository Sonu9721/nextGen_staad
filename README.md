# Mini STAAD - NextGen facade analysis

Read the [complete implementation and industry user guide](docs/INDUSTRY_AND_USER_GUIDE.md), or download the [PDF manual](docs/Mini_STAAD_Implementation_and_Industry_Guide.pdf). It covers setup, a hand calculation tutorial, facade use cases, result interpretation, API integration, testing and adoption limits.

Version 0.3.0: standalone 3D frame analysis with concentrated member moments, continuous physical-response envelopes, the original job API, three extraction flows, seven original supplied models plus an approved Sample 7 revision, a local model-review interface and quantitative reference reports. STAAD.Pro is optional.

**Validation build:** 156 of 164 evaluated reference values meet the unchanged tolerances, including all 40 from Sample 6. Eight entries from Samples 1–3 still differ. Sample 5 has no reference. Sample 7's 40 reference entries cannot be verified because its original model has a loaded vertical mechanism; it is attached unchanged with an actionable diagnostic. The separately approved revision removes only four start FX releases and solves all six cases with unchanged supports. Select **07R Sample 7 - Approved revision**. Its peak absolute movement is 121.329 mm, requiring project-specific serviceability review; no independent reference result is supplied for the changed model. Read the [Sample 6/7 report](docs/SAMPLE_6_7_VALIDATION.md) and [validation results](docs/VALIDATION_RESULTS.md). This is not a certified STAAD replacement.

## Start on Windows

1. Python 3.10 or newer is required.
2. Double-click start_api.bat. First setup downloads dependencies to a project-local .venv.
3. Open http://127.0.0.1:8000. Choose an example or upload a .std, select the extraction flow and run analysis.

The console shows undeformed geometry, supports, governing members and profile envelopes. Its metric cards default to physical response: all members, continuous peaks and shear deformation. The result-definition selector offers the original extraction values; profile tables retain the legacy definitions. Both appear in exported JSON. Keep the server window open; Ctrl+C stops it. Files stay under runtime/ by default. The launcher binds only to localhost.

## Command line

```powershell
.\.venv\Scripts\python.exe -m engine examples/sample4/Casement_CF-3T3S-2F_STAAD.std --flow casement --output result.json
```

Linux/macOS: create a virtual environment, install requirements.txt, then run `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.

## API compatibility

* GET /health?check=server|license|staad
* POST /jobs: multipart std_file, extraction_flow, optional generation_request, existing timeout/retry/path fields
* GET /jobs/{job_id}: status and progress
* GET /jobs/{job_id}/result: original job wrapper and global/profile JSON
* POST /jobs/{job_id}/abort: queued/running cancellation

Output: kN, mm, kN-m. In-house stations: metres. Analysis backend/version/validation status/diagnostics and `physical_response` are additive. `properties` and `casement` preserve existing integration semantics. The console adds /, /examples/{sample}, /jobs/{job_id}/model and /validation-report without replacing production job endpoints.

ANALYSIS_BACKEND=inhouse is the default. For the legacy oracle, install requirements-openstaad.txt and set ANALYSIS_BACKEND=openstaad on a licensed Windows host. In-house mode does not launch, attach to or terminate Bentley processes. Deterministic analysis/input errors are not retried. Run one API process because jobs remain in memory.

Automated verification: **244 tests passed, one licensed oracle test skipped; 15 HTTP workflow groups passed.** This covers analytical solutions, reference contracts, API workflows and rejection of unstable models. See the current [build verification](docs/BUILD_VERIFICATION.md) for test totals and HTTP evidence. The [v0.2 accuracy retest report](docs/ACCURACY_RETEST_REPORT.md) is retained as historical evidence.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/validate_samples.py
.\.venv\Scripts\python.exe scripts/validate_sample7_revision.py
.\.venv\Scripts\python.exe scripts/retest_e2e.py
```

The reference validator's --strict option intentionally returns failure while numerical differences or missing references remain. A licensed live oracle test is skipped unless RUN_OPENSTAAD_ORACLE=1 and OPENSTAAD_ORACLE_MODEL are explicitly set.

## Project guide

* [Current architecture](docs/CURRENT_ARCHITECTURE.md)
* [Requirements](docs/INHOUSE_SOLVER_REQUIREMENTS.md)
* [Solver architecture and sources](docs/INHOUSE_SOLVER_ARCHITECTURE.md)
* [Supported syntax](docs/SUPPORTED_STAAD_FEATURES.md)
* [Validation results](docs/VALIDATION_RESULTS.md)
* [Known limitations](docs/KNOWN_LIMITATIONS.md)
* [Original API documentation](docs/LEGACY_API_README.md)

Engine code is in engine/. Original classifiers/envelopes remain in staad-max-extractor/. Supplied models/references are in examples/ and reproducible comparisons in validation/. No sample results are embedded in solver calculations.
