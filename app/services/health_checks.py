from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from app.core.config import Settings
from app.schemas import HealthResponse
from app.services.staad_runner import ExtractorExecutionError, run_staad_preflight

BENTLEY_CONNECTION_CLIENT_ROOT = Path(r"C:\Program Files\Common Files\Bentley Shared\CONNECTION Client")
STAAD_LICENSE_PRODUCT_IDS = (1562, 2642, 1553)
LICENSE_HEALTH_TIMEOUT_SECONDS = 15
LICENSE_HEALTH_OK_STATUSES = {"ok", "trial"}


def build_server_health(*, manager: Any) -> HealthResponse:
    worker_tasks = getattr(manager, "worker_tasks", [])
    active_workers = len([task for task in worker_tasks if not task.done()])
    queue = getattr(manager, "queue", None)
    queue_size = queue.qsize() if queue is not None else None

    return HealthResponse(
        check="server",
        status="ok",
        healthy=True,
        message="FastAPI server is ready.",
        details={
            "queue_size": queue_size,
            "configured_worker_count": getattr(getattr(manager, "settings", None), "staad_worker_count", None),
            "effective_worker_count": manager.effective_worker_count(),
            "active_worker_count": active_workers,
            "allow_parallel_staad": getattr(getattr(manager, "settings", None), "allow_parallel_staad", None),
        },
    )


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _build_license_probe_script(product_ids: tuple[int, ...]) -> str:
    product_ids_literal = ",".join(str(product_id) for product_id in product_ids)
    return rf"""
$ErrorActionPreference = 'Stop'
$connectionClientRoot = '{BENTLEY_CONNECTION_CLIENT_ROOT}'
$connectApi = Join-Path $connectionClientRoot 'Bentley.Connect.Client.API.dll'
$licenseApi = Join-Path $connectionClientRoot 'LicService\Bentley.Licensing.Client.API.dll'
if (-not (Test-Path $connectApi)) {{
    throw "Bentley Connection Client API not found: $connectApi"
}}
if (-not (Test-Path $licenseApi)) {{
    throw "Bentley Licensing Client API not found: $licenseApi"
}}
Add-Type -Path $connectApi
Add-Type -Path $licenseApi
$connect = New-Object Bentley.Connect.Client.API.V1.ConnectClientAPI
$license = New-Object Bentley.Licensing.Client.API.V1.ServiceClientAPI
$productIds = @({product_ids_literal})
$products = foreach ($productId in $productIds) {{
    $productName = $null
    $entitlementCount = 0
    $licenseStatus = $null
    try {{
        $productName = $license.GetProductName($productId)
    }} catch {{}}
    try {{
        $entitlementCount = @($license.GetEntitlements($productId)).Count
    }} catch {{}}
    try {{
        $statusObj = $license.GetStatus($productId, '')
        if ($null -ne $statusObj) {{
            $licenseStatus = $statusObj.ToString()
        }}
    }} catch {{}}
    [pscustomobject]@{{
        product_id = $productId
        product_name = $productName
        entitlement_count = $entitlementCount
        status = $licenseStatus
    }}
}}
[pscustomobject]@{{
    desktop_api_installed = [bool]$connect.IsDesktopClientApiInstalled()
    connect_running = [bool]$connect.IsRunning()
    connect_logged_in = [bool]$connect.IsLoggedIn()
    user_session_active = [bool]$connect.IsUserSessionActive()
    eula_accepted = [bool]$connect.HasUserAcceptedEULA()
    license_service_installed = [bool]$license.IsLicenseServiceInstalled()
    has_valid_access_key = [bool]$license.HasValidAccessKey()
    products = $products
}} | ConvertTo-Json -Depth 6 -Compress
"""


def _run_bentley_license_probe(*, timeout_seconds: int = LICENSE_HEALTH_TIMEOUT_SECONDS) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("Bentley Connection Client licensing checks are only supported on Windows hosts.")

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        _build_license_probe_script(STAAD_LICENSE_PRODUCT_IDS),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_seconds)),
        check=False,
    )
    if completed.returncode != 0:
        stderr_text = _normalize_text(completed.stderr)
        stdout_text = _normalize_text(completed.stdout)
        message = stderr_text or stdout_text or f"Bentley licensing probe exited with code {completed.returncode}."
        raise RuntimeError(message)

    payload_text = _normalize_text(completed.stdout)
    if not payload_text:
        raise RuntimeError("Bentley licensing probe returned no output.")

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Bentley licensing probe returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Bentley licensing probe returned an unexpected payload.")
    return payload


def _safe_license_details(payload: dict[str, Any]) -> dict[str, Any]:
    product_details = []
    for product in payload.get("products") or []:
        if not isinstance(product, dict):
            continue
        product_details.append(
            {
                "product_id": product.get("product_id"),
                "product_name": product.get("product_name"),
                "status": product.get("status"),
                "entitlement_count": product.get("entitlement_count"),
            }
        )

    return {
        "desktop_api_installed": bool(payload.get("desktop_api_installed")),
        "connect_running": bool(payload.get("connect_running")),
        "connect_logged_in": bool(payload.get("connect_logged_in")),
        "user_session_active": bool(payload.get("user_session_active")),
        "eula_accepted": bool(payload.get("eula_accepted")),
        "license_service_installed": bool(payload.get("license_service_installed")),
        "has_valid_access_key": bool(payload.get("has_valid_access_key")),
        "products": product_details,
    }


def _build_license_message(details: dict[str, Any]) -> tuple[bool, str]:
    products = details.get("products") or []
    has_entitlement = any(
        int(product.get("entitlement_count") or 0) > 0
        and _normalize_text(product.get("status")).lower() in LICENSE_HEALTH_OK_STATUSES
        for product in products
    )
    has_access_key = bool(details.get("has_valid_access_key"))

    if not details.get("desktop_api_installed"):
        return False, "Bentley Connection Client API is not installed on this host."
    if not details.get("license_service_installed"):
        return False, "Bentley licensing service is not installed or available."
    if not details.get("connect_running"):
        return False, "Bentley Connection Client is not running in the active Windows session."
    if not details.get("connect_logged_in"):
        return False, "Bentley Connection Client is not logged in."
    if not details.get("user_session_active"):
        return False, "Bentley user session is not active in the current desktop session."
    if not details.get("eula_accepted"):
        return False, "Bentley EULA has not been accepted in the active desktop session."
    if not has_entitlement and not has_access_key:
        return False, "No STAAD entitlement or valid Bentley access key is currently available."
    return True, "Bentley licensing and STAAD entitlement checks passed."


def run_license_health_check(*, settings: Settings) -> HealthResponse:
    if settings.analysis_backend == "inhouse":
        return HealthResponse(check="license",status="healthy",healthy=True,
            message="In-house analysis does not require a Bentley license.",details={"backend":"inhouse","license_required":False})
    del settings

    try:
        payload = _run_bentley_license_probe(timeout_seconds=LICENSE_HEALTH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return HealthResponse(
            check="license",
            status="error",
            healthy=False,
            message="Bentley licensing probe timed out.",
            details={
                "error_code": "license_probe_timeout",
                "retryable": True,
                "hint": "Verify Bentley licensing services are responsive in the logged-in Windows session.",
            },
        )
    except Exception as exc:
        return HealthResponse(
            check="license",
            status="error",
            healthy=False,
            message="Bentley licensing probe failed.",
            details={
                "error_code": "license_probe_failed",
                "retryable": True,
                "hint": "Verify Connection Client and Bentley licensing components are installed and reachable.",
                "exception_type": exc.__class__.__name__,
                "message": _normalize_text(exc),
            },
        )

    details = _safe_license_details(payload)
    healthy, message = _build_license_message(details)
    return HealthResponse(
        check="license",
        status="healthy" if healthy else "unhealthy",
        healthy=healthy,
        message=message,
        details=details,
    )


def _safe_staad_error(exc: ExtractorExecutionError) -> dict[str, Any]:
    details = {
        "error_code": exc.error_code,
        "retryable": exc.retryable,
        "attempts": exc.attempts,
    }
    if exc.stage:
        details["stage"] = exc.stage
    if exc.hint:
        details["hint"] = exc.hint
    return details


def run_staad_readiness_check(*, settings: Settings) -> HealthResponse:
    if settings.analysis_backend == "inhouse":
        try:
            from engine.adapter import extractor_module
            from engine.solver import solve
            extractor_module()
            return HealthResponse(check="staad",status="healthy",healthy=True,
                message="In-house parser, solver and result adapter are ready.",details={"backend":"inhouse","element_theory":"Timoshenko / Euler-Bernoulli"})
        except Exception as exc:
            return HealthResponse(check="staad",status="error",healthy=False,
                message="In-house engine dependencies are unavailable.",details={"backend":"inhouse","exception_type":type(exc).__name__})
    try:
        payload = run_staad_preflight(settings=settings, force=True)
    except ExtractorExecutionError as exc:
        return HealthResponse(
            check="staad",
            status="unhealthy" if not exc.retryable else "error",
            healthy=False,
            message=exc.message,
            details=_safe_staad_error(exc),
        )
    except Exception as exc:
        return HealthResponse(
            check="staad",
            status="error",
            healthy=False,
            message="STAAD readiness check failed unexpectedly.",
            details={
                "error_code": "staad_readiness_failed",
                "retryable": False,
                "hint": "See server logs for the full STAAD readiness failure details.",
                "exception_type": exc.__class__.__name__,
            },
        )

    return HealthResponse(
        check="staad",
        status="healthy",
        healthy=True,
        message="STAAD/OpenSTAAD readiness check passed.",
        details={
            "staad_executable": payload.get("staad_executable"),
            "current_file": payload.get("current_file"),
        },
    )
