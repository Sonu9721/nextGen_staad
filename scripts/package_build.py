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
            f"Mini STAAD v{__version__} — retested validation build\n\n1. Install Python 3.10 or newer.\n2. Double-click Start MiniSTAAD.bat. First setup downloads dependencies.\n3. Open http://127.0.0.1:8000.\n\n{passed} automated tests and {len(e2e['checks'])} HTTP workflow groups passed; {skipped} licensed oracle test skipped. {differences} evaluated reference entries remain outside tolerance. Models rejected pending support review: {', '.join(blocked) or 'none'}. Rejection of an unstable model is a successful software check, not a successful structural analysis. Read staad-report-extractor/docs/SAMPLE_6_7_VALIDATION.md.\n\nThe new archive's two models and original references are included unchanged. Sample 6 also has a separate normalized reference.json. Physical-response cards include shear and continuous peaks. Legacy integration fields and profile tables retain the original reporting definitions. This package does not claim universal accuracy or STAAD.Pro certification.\n",
        )
        count += 2
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
