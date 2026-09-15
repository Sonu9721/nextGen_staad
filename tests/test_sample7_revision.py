"""Verify the approved axial connection change and the resulting load path."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from engine.parser import parse_std
from engine.solver import solve

ROOT = Path(__file__).parents[1]
ORIGINAL = ROOT / "examples/sample7/sample_7.std"
REVISED = ROOT / "examples/revisions/sample7/sample_7_axial_connected.std"
BRIDGES = (5, 16, 27, 38)
PINS = (7, 11, 19, 23, 31, 35, 43, 47)


@pytest.fixture(scope="module")
def solution():
    return solve(parse_std(REVISED))


def test_revision_changes_only_four_approved_axial_releases():
    source = json.loads(REVISED.with_name("source.json").read_text())
    expected = ORIGINAL.read_bytes()
    assert hashlib.sha256(expected).hexdigest() == source["original_sha256"]
    for mid in BRIDGES:
        expected = expected.replace(
            f"{mid} START FX MY MZ".encode(), f"{mid} START MY MZ".encode(), 1
        )
    assert REVISED.read_bytes() == expected
    assert hashlib.sha256(expected).hexdigest() == source["revised_sha256"]
    assert parse_std(REVISED).supports == parse_std(ORIGINAL).supports


def test_dead_load_balances_independently_summed_weights_and_applied_forces(solution):
    model = solution.model
    dead = model.cases[1]
    assert dead.selfweight == [("Y", -1.0)]
    weight = sum(
        model.materials[m.material].density * m.section.ax * model.length(m.id)
        for m in model.members.values()
    )
    gravity_forces = [p for p in dead.member if not p.moment]
    assert all(p.point and p.direction == "GY" for p in gravity_forces)
    total_load = weight - sum(p.qa for p in gravity_forces)
    assert total_load == pytest.approx(26159.76912288, abs=1e-7)
    reactions = solution.cases[1].reactions.reshape(-1, 6)
    upward = sum(reactions[solution.node_indices[n], 1] for n in PINS)
    assert upward == pytest.approx(total_load, abs=1e-7)
    # Cut the lower assembly at the four connections. Its known gravity load
    # must be carried upward through the restored axial actions alone.
    bridge_force = -sum(solution.cases[1].members[mid].end_forces[0] for mid in BRIDGES)
    assert bridge_force == pytest.approx(11994.59676816, abs=1e-7)
    assert all(reactions[solution.node_indices[n], 1] > 0 for n in PINS)


@pytest.mark.parametrize("case_id", [1, 2, 3, 4, 5, 6])
def test_every_case_preserves_support_freedoms_and_remaining_releases(
    solution, case_id
):
    case = solution.cases[case_id]
    assert np.isfinite(case.displacement).all()
    assert solution.diagnostics["minimum_scaled_eigenvalue"] > 1e-12
    for node, restrained in solution.model.supports.items():
        i = 6 * solution.node_indices[node]
        np.testing.assert_allclose(
            case.displacement[i + np.array(sorted(restrained))], 0, atol=1e-12
        )
        free = np.array(sorted(set(range(6)) - restrained))
        np.testing.assert_allclose(case.reactions[i + free], 0, atol=1e-8)
    for mid in BRIDGES:
        np.testing.assert_allclose(case.members[mid].end_forces[[4, 5]], 0, atol=1e-8)
    for mid in (8, 19, 30, 41):
        np.testing.assert_allclose(
            case.members[mid].end_forces[[0, 4, 5]], 0, atol=1e-8
        )
    balance = solution.diagnostics["equilibrium"][case_id]
    assert balance["relative_force_error"] < 1e-9
    assert balance["relative_moment_error"] < 1e-9


def test_wind_reaches_both_support_groups_without_vertical_reaction_at_sliding_supports(
    solution,
):
    case = solution.cases[2]
    for group in [PINS, set(solution.model.supports) - set(PINS)]:
        assert all(
            abs(case.reactions[6 * solution.node_indices[n] + 2]) > 1 for n in group
        )
    for n in set(solution.model.supports) - set(PINS):
        assert case.reactions[6 * solution.node_indices[n] + 1] == 0
