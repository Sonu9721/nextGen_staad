"""Reproduce the approved Sample 7 revision, reactions and physical envelopes.

The original model and its reference are preserved. Their reference counts do
not apply to a model with changed releases.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.adapter import build_payload, make_config
from engine.errors import AnalysisError
from engine.parser import parse_std
from engine.solver import solve
from engine import __version__


def main():
    folder = ROOT / "examples/revisions/sample7"
    source = json.loads((folder / "source.json").read_text())
    original = ROOT / source["original_model"]
    path = folder / source["revised_model"]
    assert (
        hashlib.sha256(original.read_bytes()).hexdigest() == source["original_sha256"]
    )
    expected = original.read_bytes()
    for change in source["changes"]:
        before = f"{change['member']} START {change['before']}".encode()
        after = f"{change['member']} START {change['after']}".encode()
        assert expected.splitlines().count(before) == 1
        expected = expected.replace(before, after, 1)
    assert path.read_bytes() == expected
    assert hashlib.sha256(expected).hexdigest() == source["revised_sha256"]
    original_model = parse_std(original)
    try:
        solve(original_model)
    except AnalysisError as error:
        assert error.code == "SINGULAR_MATRIX"
    else:
        raise AssertionError("The unchanged original must still be rejected")
    solution = solve(parse_std(path))
    assert solution.model.supports == original_model.supports
    assert set(solution.cases) == {1, 2, 3, 4, 5, 6}
    payload = build_payload(solution, make_config(path, "casement"))
    payload["file"] = path.relative_to(ROOT).as_posix()
    cases = {}
    for cid, case in sorted(solution.cases.items()):
        balance = solution.diagnostics["equilibrium"][cid]
        assert balance["relative_force_error"] < 1e-9
        assert balance["relative_moment_error"] < 1e-9
        reactions = {}
        for node, dofs in sorted(solution.model.supports.items()):
            i = 6 * solution.node_indices[node]
            values = case.reactions[i : i + 6]
            free = sorted(set(range(6)) - dofs)
            np.testing.assert_allclose(values[free], 0, atol=1e-8)
            reactions[node] = dict(
                zip(["FX", "FY", "FZ", "MX", "MY", "MZ"], (values / 1000).tolist())
            )
        cases[cid] = {
            "title": solution.model.titles[cid],
            "support_reactions": reactions,
            "sum_vertical_reaction_kN": sum(r["FY"] for r in reactions.values()),
            "start_axial_force_kN": {
                m: float(case.members[m].end_forces[0] / 1000) for m in (5, 16, 27, 38)
            },
            "equilibrium": balance,
        }
    report = {
        "status": "pass",
        "model_revision": source["model_revision"],
        "solver_version": __version__,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "original_unchanged_and_rejected": True,
        "revision_sha256": source["revised_sha256"],
        "only_approved_changes": True,
        "supports_unchanged": True,
        "support_reaction_units": {"force": "kN", "moment": "kN-m", "axes": "global"},
        "member_start_axial_force_axes": "local; action on member at its start",
        "equilibrium_residual_units": {"force_N": "N", "moment_Nm": "N-m"},
        "reference_status": source["reference_status"],
        "diagnostics": solution.diagnostics,
        "cases": cases,
        "physical_response": payload["physical_response"],
    }
    for name, data in [
        ("sample7-revised.json", payload),
        ("sample7-revision-check.json", report),
    ]:
        (ROOT / "validation" / name).write_text(
            json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "status": "pass",
                "load_cases": sorted(solution.cases),
                "gravity_reaction_kN": cases[1]["sum_vertical_reaction_kN"],
                "physical_response": payload["physical_response"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
