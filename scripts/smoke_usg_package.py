"""Verify the actual release archive from an independently extracted code tree."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import __version__


def main():
    archive = ROOT.parent / f"MiniSTAAD_v{__version__}_Retested.zip"
    verification = json.loads((ROOT / "validation/usg-verification.json").read_text())
    report = {
        "version": __version__,
        "status": "fail",
        "dependencies": "Existing project virtual environment; no dependencies bundled",
        "models": {},
    }
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="usg-package-", dir=ROOT / "tmp"
    ) as directory:
        destination = Path(directory).resolve()
        with zipfile.ZipFile(archive) as z:
            assert z.testzip() is None
            for member in z.infolist():
                target = (destination / member.filename).resolve()
                assert target.is_relative_to(destination), "Unsafe package entry"
                assert not any(
                    part in {".venv", "runtime", "maas"}
                    for part in Path(member.filename).parts
                )
                assert "sample_7_axial_connected" not in member.filename
            z.extractall(destination)
        extracted = destination / "MiniSTAAD/staad-report-extractor"
        for name, digest in verification["tested_source_files_sha256"].items():
            assert hashlib.sha256((extracted / name).read_bytes()).hexdigest() == digest
        env = {**os.environ, "PYTHONPATH": str(extracted)}
        probe = subprocess.run(
            [sys.executable, "-c", "import engine;print(engine.__file__)"],
            cwd=extracted,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert Path(probe.stdout.strip()).resolve().is_relative_to(extracted)
        report["imports_from_extracted_copy"] = True
        for name in ("USG_1", "USG_2"):
            output = destination / f"{name}-result.json"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "engine",
                    str(extracted / "examples/usg" / f"{name}.std"),
                    "--flow",
                    "fully_unitized",
                    "--output",
                    str(output),
                ],
                cwd=extracted,
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=180,
            )
            actual = json.loads(output.read_text())
            expected = json.loads(
                (ROOT / "validation/usg" / f"{name}-actual.json").read_text()
            )
            for key in ("properties", "profiles", "physical_response"):
                assert actual[key] == expected[key], (name, key)
            assert actual["analysis"]["solver_version"] == __version__
            report["models"][name] = (
                "Exact properties, profile counts and physical_response match"
            )
            print("PASS extracted package", name, flush=True)
        manifest = json.loads(
            (extracted / "validation/usg-source-manifest.json").read_text()
        )
        for entry in manifest["files"]:
            assert (
                hashlib.sha256((extracted / entry["path"]).read_bytes()).hexdigest()
                == entry["sha256"]
            )
    report.update(
        status="pass",
        archive_integrity="pass",
        usg_source_files_unchanged=True,
        excluded=["CAD/MAAS code and dependencies", "reverted Sample 7 revision"],
        tested_source_files_sha256=verification["tested_source_files_sha256"],
    )
    (ROOT / "validation/package-smoke.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
