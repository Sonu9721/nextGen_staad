"""Create a portable source package only after successful verification."""

import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import __version__


def main():
    suite = ET.parse(ROOT / "validation/retest.xml").getroot().find("testsuite")
    assert (
        suite is not None
        and int(suite.get("failures", 0)) == 0
        and int(suite.get("errors", 0)) == 0
    )
    e2e = json.loads((ROOT / "validation/e2e.json").read_text())
    assert e2e["status"] == "pass"
    comparison = json.loads(
        (ROOT / "validation/comparison.json").read_text(encoding="utf-8")
    )
    passed = int(suite.get("tests", 0)) - int(suite.get("skipped", 0))
    skipped = int(suite.get("skipped", 0))
    differences = sum(
        r.get("counts", {}).get("difference", 0) for r in comparison.values()
    )
    blocked = [
        name for name, r in comparison.items() if r.get("analysis_status") == "rejected"
    ]
    assert e2e.get("solver_version") == __version__, (
        "HTTP evidence is from a different build"
    )
    source_manifest = json.loads(
        (ROOT / "validation/usg-source-manifest.json").read_text()
    )
    for entry in source_manifest["files"]:
        assert (
            hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest()
            == entry["sha256"]
        )
    assert {"usg1", "usg2"} <= set(e2e["samples"]), "USG HTTP evidence is missing"
    usg = json.loads((ROOT / "validation/usg/comparison.json").read_text())["models"]
    assert all(r["solver_version"] == __version__ for r in usg.values())
    verification = json.loads((ROOT / "validation/usg-verification.json").read_text())
    assert verification["status"] == "pass" and verification["version"] == __version__
    for name, digest in verification["tested_source_files_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, (
            f"Source changed after verification: {name}"
        )
    target = ROOT.parent / f"MiniSTAAD_v{__version__}_Retested.zip"
    ignored = {
        ".venv",
        ".git",
        ".idea",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "runtime",
        ".agents",
        ".codex",
    }
    directories = {
        "app",
        "engine",
        "tests",
        "staad-max-extractor",
        "scripts",
        "docs",
        "examples",
        "validation",
    }
    root_files = {
        "README.md",
        "start_api.bat",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-docs.txt",
        "requirements-openstaad.txt",
        "requirements-tested.txt",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        "pytest.ini",
        "conftest.py",
        "LICENSE",
        "LICENSE.md",
    }
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.is_symlink() or set(relative.parts) & ignored:
                continue
            if (
                relative.parts[0] not in directories
                and relative.as_posix() not in root_files
            ):
                continue
            if (
                path.name == "retest-baseline.xml"
                or path.name == ".env"
                or path.suffix in {".log", ".pyc", ".zip"}
            ):
                continue
            archive.write(
                path, "MiniSTAAD/staad-report-extractor/" + relative.as_posix()
            )
            count += 1
        archive.writestr(
            "MiniSTAAD/Start MiniSTAAD.bat",
            '@echo off\r\ncall "%~dp0staad-report-extractor\\start_api.bat"\r\n',
        )
        archive.writestr(
            "MiniSTAAD/START_HERE.txt",
            f"Mini STAAD v{__version__} - USG validation build\n\n"
            "1. Install Python 3.10 or newer.\n2. Double-click Start MiniSTAAD.bat. First setup downloads dependencies.\n3. Open http://127.0.0.1:8000 and choose U1 or U2.\n\n"
            f"{passed} automated tests and {len(e2e['checks'])} HTTP workflow groups passed; {skipped} licensed oracle test skipped.\n"
            f"Original sample audit: {differences} evaluated reference differences remain. Rejected models: {', '.join(blocked) or 'none'}. Rejection is a successful software check, not a successful structural analysis.\n"
            "USG audit: both models pass 28/28 existing numerical checks, with eight stricter differences per model.\n\n"
            "Read staad-report-extractor/docs/USG_REAUDIT_REPORT.md and USG_REQUIREMENT_AUDIT.md for changes, all 71 requirements, tests, use instructions and remaining gaps. The actual NextGen generator and exact displacement equivalence remain unverified. Physical and legacy result definitions are explicitly separate. No claim of universal accuracy or STAAD.Pro certification is made.\n",
        )
        archive.writestr(
            "MiniSTAAD/USG_README.txt",
            "USG-only second audit. All earlier September 15 changes, including the Sample 7 revision and CAD/MAAS work, remain reverted.\n\n"
            "Choose U1 or U2 for the two unchanged original models from USG_1.zip. Read staad-report-extractor/docs/USG_REAUDIT_REPORT.md, USG_REQUIREMENT_AUDIT.md and USG_VALIDATION.md. The complete product request remains partly unverified; it is not made complete by passing software tests.\n",
        )
        count += 3
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "archive": str(target),
                "files": count,
                "bytes": target.stat().st_size,
                "sha256": digest,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
