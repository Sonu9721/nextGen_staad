"""Second audit regressions, with closed-form rather than fitted references."""

import json

import numpy as np
import pytest
from test_engine import beam

from engine.comparison import compare, reference_point_value
from engine.solver import solve


@pytest.mark.parametrize("axis,moment", [("Y", "MZ"), ("Z", "MY")])
@pytest.mark.parametrize("intensity", [1e-12, 1e-6, 1e3, 1e12])
def test_moment_extremum_is_independent_of_load_scale(axis, moment, intensity):
    result = solve(
        beam(
            support="1 PINNED\n2 FIXED BUT FX MY MZ",
            load=f"MEMBER LOAD\n1 UNI G{axis} {-intensity}",
        )
    )
    low, xlow, high, xhigh = result.cases[1].members[1].extrema(moment)
    value, station = max(((abs(low), xlow), (abs(high), xhigh)))
    assert value == pytest.approx(intensity * 4**2 / 8, rel=1e-11, abs=0)
    assert station == pytest.approx(2, abs=1e-10)


@pytest.mark.parametrize("location", ["start", "end"])
@pytest.mark.parametrize(
    "path",
    [
        "properties.global.displacement",
        "properties.transom.displacement",
        "casement.df.all",
    ],
)
def test_reference_member_endpoint_displacement_is_not_a_force(path, location):
    result = solve(beam())
    # Cantilever: fixed end is zero; free end is PL^3/(3EI).
    expected = (
        0
        if location == "start" or path.startswith("casement.")
        else 1000 * 4**3 / (3 * 200e9 * 4e-5) * 1000
    )
    point = {"load_case": 1, "member_id": 1, "location": location, "axis": "RESULTANT"}
    assert reference_point_value(path, point, result, "METER") == pytest.approx(
        expected, abs=1e-11
    )


@pytest.mark.parametrize(
    "station", [float("nan"), float("inf"), "not-a-distance", True]
)
def test_invalid_reference_station_remains_unavailable(station):
    result = solve(beam())
    point = {
        "value": 1.0,
        "load_case": 1,
        "member_id": 1,
        "station": station,
        "station_unit": "m",
        "axis": "MZ",
    }
    row = compare(
        {"bending_moment": point}, {"bending_moment": {**point, "station": 1}}, result
    )["metrics"][0]
    assert row["reference_station_m"] is None
    assert row["candidate_at_reference_point"] is None
    assert row["reference_point_status"] == "unavailable"
    json.dumps(row, allow_nan=False)


def test_unknown_reference_force_axis_remains_unavailable():
    result = solve(beam())
    point = {"load_case": 1, "member_id": 1, "location": "end", "axis": "INVALID"}
    assert reference_point_value("bending_moment.major", point, result, "METER") is None


def test_nodal_reference_displacement_matches_the_closed_form():
    result = solve(beam())
    point = {"load_case": 1, "node_id": 2, "source": "node", "axis": "RESULTANT"}
    value = reference_point_value(
        "properties.global.displacement", point, result, "METER"
    )
    assert value == pytest.approx(1000 * 4**3 / (3 * 200e9 * 4e-5) * 1000)
    assert np.isfinite(value)


def test_renumbered_custom_std_preserves_complete_envelopes_and_metadata(tmp_path):
    from test_unitized_customization import custom_unitized

    from engine.adapter import analyze_file

    results = []
    for offset in (0, 5000):
        text, _ = custom_unitized(
            [1.1, 1.6],
            [[1.3, 2.7], [1.2, 2.2]],
            individual_pris=True,
            node_base=101 + offset,
            member_base=301 + offset,
        )
        path = tmp_path / f"custom-{offset}.std"
        path.write_text(text)
        results.append(analyze_file(path, flow="fully_unitized"))

    def original_ids(value):
        if isinstance(value, dict):
            return {
                k: v - 5000
                if k in {"member_id", "node_id"} and isinstance(v, int)
                else original_ids(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [original_ids(v) for v in value]
        return value

    for key in ("properties", "profiles", "physical_response"):
        assert original_ids(results[1][key]) == results[0][key]
    assert results[1]["analysis"]["load_cases"] == [7, 13, 21, 22]
