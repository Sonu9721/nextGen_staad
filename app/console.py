"""Optional local review console; existing job endpoints remain unchanged."""

from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from app.services.model_geometry import read_model_geometry
from engine.errors import AnalysisError

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(include_in_schema=False)


@router.get("/")
def index():
    return FileResponse(ROOT / "app/web/index.html")


@router.get("/console.js")
def script():
    return FileResponse(
        ROOT / "app/web/console.js", media_type="application/javascript"
    )


@router.get("/console.css")
def style():
    return FileResponse(ROOT / "app/web/console.css", media_type="text/css")


@router.get("/examples/{sample}")
def example(sample: str):
    if sample == "sample7-revised":
        path = ROOT / "examples/revisions/sample7/sample_7_axial_connected.std"
        if not path.is_file():
            raise HTTPException(404, "Example not installed")
        return FileResponse(path, filename=path.name, media_type="text/plain")
    if sample not in {f"sample{i}" for i in range(1, 8)}:
        raise HTTPException(404, "Example not found")
    paths = list((ROOT / "examples" / sample).glob("*.std"))
    if not paths:
        raise HTTPException(404, "Example not installed")
    return FileResponse(paths[0], filename=paths[0].name, media_type="text/plain")


@router.get("/validation-report")
def validation():
    return PlainTextResponse(
        (ROOT / "docs/VALIDATION_RESULTS.md").read_text(encoding="utf-8")
    )


@router.get("/jobs/{job_id}/model")
async def model(job_id: str):
    from app.main import manager

    record = await manager.get_job(job_id)
    if record is not None and record.model_geometry is not None:
        return record.model_geometry
    if record is None or record.std_file is None or not record.std_file.is_file():
        raise HTTPException(404, "Model unavailable")
    import asyncio

    try:
        return await asyncio.to_thread(read_model_geometry, record.std_file)
    except AnalysisError as e:
        raise HTTPException(422, {"error_code": e.code, "message": str(e)}) from e
