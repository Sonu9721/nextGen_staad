import json
from pathlib import Path
import pytest
from engine.parser import parse_std
from engine.solver import solve
from engine.adapter import make_config, build_payload
from engine.comparison import compare

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "sample", ["sample1", "sample2", "sample3", "sample4", "sample5", "sample6"]
)
def test_supplied_models_contract_equilibrium_and_reference_points(sample):
    folder = ROOT / "examples" / sample
    path = next(folder.glob("*.std"))
    result = solve(parse_std(path))
    flow = (
        "fully_unitized" if sample in {"sample1", "sample2", "sample3"} else "casement"
    )
    payload = build_payload(result, make_config(path, flow))
    assert payload["analysis"]["load_cases"] == [1, 2, 3, 4]
    assert payload["units"]["output"] == {
        "force": "kN",
        "length": "mm",
        "moment": "kN-m",
    }
    assert result.diagnostics["relative_residual"] < 1e-9
    json.dumps(payload, allow_nan=False)
    if (folder / "output.txt").stat().st_size:
        reference_path = (
            folder / "reference.json"
            if (folder / "reference.json").exists()
            else folder / "output.txt"
        )
        ref = json.loads(reference_path.read_text(encoding="utf-8"))["result"]
        comparison = compare(ref, payload, result)
        assert comparison["counts"]["missing"] == 0
        row = next(
            r
            for r in comparison["metrics"]
            if r["metric"] == "properties.bending_moment.major"
        )
        assert row["candidate_at_reference_point"] == pytest.approx(
            row["reference"], abs=0.00002
        )
        if flow == "fully_unitized":
            assert payload["profiles"] == ref["profiles"]
        else:
            for group in ref["casement"]["profiles"]:
                assert (
                    payload["casement"]["profiles"][group]["member_ids"]
                    == ref["casement"]["profiles"][group]["member_ids"]
                )
            assert comparison["all_values_within_tolerance"]


def test_combinations_can_be_excluded():
    path = next((ROOT / "examples/sample4").glob("*.std"))
    result = solve(parse_std(path))
    payload = build_payload(result, make_config(path, "standard", False))
    assert payload["analysis"]["load_cases"] == [1, 2]
    assert payload["analysis"]["include_combinations"] is False
