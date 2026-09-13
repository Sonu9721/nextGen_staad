"""Keep legacy extractor imports available without shadowing the API package."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
for directory in (ROOT / "staad-max-extractor", ROOT / "tests"):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))
