import hashlib
import json
from pathlib import Path

import pytest

from engine.comparison import compare, reference_point_value
from engine.parser import parse_std
from engine.solver import solve

ROOT = Path(__file__).parents[1]
MANIFEST = json.loads(
    (ROOT / "validation/usg-source-manifest.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("entry", MANIFEST["files"], ids=lambda entry: entry["source"])
def test_supplied_usg_source_is_unchanged(entry):
    assert (
        hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest()
        == entry["sha256"]
    )


def test_comparison_keeps_near_zero_error_and_normalizes_station_units():
    ref = {
        "force": {
            "value": 0.0,
            "member_id": 12,
            "station": 10.0,
            "station_unit": "in",
            "load_case": 7,
            "axis": "FX",
        }
    }
    actual = {
        "force": {
            "value": 0.001,
            "member_id": 12,
            "station": 0.254,
            "station_unit": "m",
            "load_case": 7,
            "axis": "FY",
        }
    }
    row = compare(ref, actual)["metrics"][0]
    assert row["relative_difference"] is None
    assert row["absolute_difference"] == 0.001
    assert row["station_difference_m"] == pytest.approx(0, abs=1e-12)
    assert row["metadata_differences"]["axis"] == {"reference": "FX", "candidate": "FY"}


def test_reference_station_is_not_silently_clamped_or_invented():
    result = solve(parse_std(ROOT / "examples/usg/USG_1.std"))
    base = {"load_case": 1, "member_id": 1, "axis": "MZ", "station_unit": "m"}
    for extra in (
        {},
        {"station": -1},
        {"station": 999},
        {"station": float("nan")},
        {"station": 1, "station_unit": "unknown"},
    ):
        assert (
            reference_point_value(
                "bending_moment.major", {**base, **extra}, result, "METER"
            )
            is None
        )
