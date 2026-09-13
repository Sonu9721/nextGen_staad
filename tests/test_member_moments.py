"""Concentrated couples: closed forms and equivalent subdivided nodal models."""

from copy import deepcopy
import numpy as np
import pytest
from engine.errors import AnalysisError
from engine.model import Member, MemberLoad
from engine.solver import solve
from engine.physical import interval_fields, intervals, build_physical_response
from test_engine import beam


@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
@pytest.mark.parametrize("station", [0, 1.3, 4])
@pytest.mark.parametrize("shear", [False, True])
def test_cantilever_couple_closed_form(axis, station, shear):
    m = beam(load=f"MEMBER LOAD\n1 CMOM {axis} 1200 {station}")
    s = m.members[1].section
    if shear:
        s.ay = s.az = 0.0001
    r = solve(m)
    mr = r.cases[1].members[1]
    index = "XYZ".index(axis)
    expected_reaction = np.zeros(6)
    expected_reaction[index + 3] = -1200
    np.testing.assert_allclose(r.cases[1].reactions[:6], expected_reaction, atol=2e-10)
    rigidity = (
        m.materials["TEST"].g * s.ix
        if axis == "X"
        else m.materials["TEST"].e * (s.iy if axis == "Y" else s.iz)
    )
    for x in [0, 0.7, 1.3, 2.1, 4]:
        rotation = np.zeros(3)
        rotation[index] = 1200 * min(x, station) / rigidity
        np.testing.assert_allclose(mr.local_rotation(x), rotation, atol=2e-15)
        displacement = np.zeros(3)
        lo = min(x, station)
        if axis != "X":
            displacement[2 if axis == "Y" else 1] = (
                (1 if axis == "Z" else -1) * 1200 * (lo * x - lo * lo / 2) / rigidity
            )
        np.testing.assert_allclose(mr.local_displacement(x), displacement, atol=2e-15)
        np.testing.assert_allclose(
            mr.translation(x, legacy_section=True), displacement, atol=2e-15
        )
        np.testing.assert_allclose(
            r.cases[2].members[1].local_rotation(x), 2 * rotation, atol=5e-15
        )
    for a, b in intervals(mr):
        forces, translations = interval_fields(mr, a, b)
        x = (a + b) / 2
        np.testing.assert_allclose([p(0.5) for p in forces], mr.internal(x), atol=2e-10)
        np.testing.assert_allclose(
            [p(0.5) for p in translations], mr.local_displacement(x), atol=2e-15
        )


@pytest.mark.parametrize("axis", ["X", "Y", "Z", "GX", "GY", "GZ"])
@pytest.mark.parametrize("release", [False, True])
def test_couple_equals_split_model_nodal_moment(axis, release):
    m = beam(
        support="1 2 FIXED",
        beta=37,
        load=f"MEMBER LOAD\n1 CMOM {axis} 1700 1.3\n1 CON GY -430 1.3",
    )
    s = m.members[1].section
    s.ay, s.az = 0.00015, 0.00009
    if release:
        m.members[1].releases = {10, 11}
    unsplit = solve(m)
    split = deepcopy(m)
    split.nodes[3] = np.array([1.3, 0.0, 0.0])
    split.members[1].end = 3
    split.members[1].releases = set()
    split.members[2] = Member(
        2, 3, 2, "TEST", deepcopy(s), 37, {10, 11} if release else set()
    )
    couple = np.zeros(3)
    couple["XYZ".index(axis[-1])] = 1700
    if not axis.startswith("G"):
        couple = unsplit.elements[1].rotation.T @ couple
    split.cases[1].member.clear()
    split.cases[1].nodal[3] = np.r_[[0.0, -430.0, 0.0], couple]
    divided = solve(split)
    np.testing.assert_allclose(
        unsplit.cases[1].reactions, divided.cases[1].reactions[:12], atol=1e-9
    )
    for x in [0, 0.4, 1.3, 2.7, 4]:
        part, distance = (1, x) if x < 1.3 else (2, x - 1.3)
        whole, segment = unsplit.cases[1].members[1], divided.cases[1].members[part]
        np.testing.assert_allclose(
            whole.local_displacement(x),
            segment.local_displacement(distance),
            atol=5e-15,
        )
        np.testing.assert_allclose(
            whole.local_rotation(x), segment.local_rotation(distance), atol=5e-15
        )
        np.testing.assert_allclose(
            whole.internal(x), segment.internal(distance), atol=1e-9
        )
    assert (
        len(unsplit.cases[1].members[1].loads) == 2
    )  # Couple and force stay distinct.


def test_both_sides_of_couple_jump_and_physical_extrema():
    result = solve(
        beam(
            support="1 PINNED\n2 FIXED BUT FX MY MZ",
            load="MEMBER LOAD\n1 CMOM Z 1000 3",
        )
    )
    m = result.cases[1].members[1]
    assert m.internal(3, side="left")[5] == pytest.approx(-750)
    assert m.internal(3)[5] == pytest.approx(250)
    assert m.extrema("MZ") == pytest.approx([-750, 3, 250, 3])
    value = build_physical_response(result, [1])["forces"]["MZ"]
    assert value["value"] == pytest.approx(0.750)
    assert value["station"] == 3 and value["station_side"] == "left"


def test_cmom_force_length_units():
    # 1 kip-ft = 1355.8179483314 Nm; 2 ft = .6096 m.
    m = beam(load="UNIT FEET KIP\nMEMBER LOAD\n1 CMOM GZ 1 2")
    load = m.cases[1].member[0]
    assert load.qa == pytest.approx(1355.8179483314)
    assert load.a == pytest.approx(0.6096)
    assert load.point and load.moment


@pytest.mark.parametrize(
    "command",
    [
        "CMOM Z 1",
        "CMOM Z 1 2 3",
        "CMOM GQ 1 2",
        "CMOM Z 1 -1",
        "CMOM Z 1 5",
        "UMOM Z 1",
    ],
)
def test_unsupported_or_invalid_couple_fails_explicitly(command):
    with pytest.raises(AnalysisError):
        solve(beam(load=f"MEMBER LOAD\n1 {command}"))


def test_distributed_moment_cannot_enter_programmatic_model():
    m = beam()
    m.cases[1].member = [MemberLoad(1, "Z", 0, 4, 1, 1, moment=True)]
    with pytest.raises(AnalysisError, match="concentrated"):
        solve(m)


@pytest.mark.parametrize("station", [0, 4])
def test_end_couple_at_fixed_support_has_no_fictitious_span_peak(station):
    r = solve(beam(support="1 2 FIXED", load=f"MEMBER LOAD\n1 CMOM Z 1200 {station}"))
    m = r.cases[1].members[1]
    extremes = m.extrema("MZ")
    assert max(abs(extremes[0]), abs(extremes[2])) < 1e-10
    assert build_physical_response(r, [1])["forces"]["MZ"]["value"] < 1e-12


@pytest.mark.parametrize("magnitude", [1e-20, 1e20])
def test_pure_couple_equilibrium_is_load_scale_invariant(magnitude):
    r = solve(beam(load=f"MEMBER LOAD\n1 CMOM Y {magnitude} 1.3"))
    expected = -magnitude * (1.3 * 4 - 1.3**2 / 2) / (200e9 * 2e-5)
    assert r.cases[1].displacement[8] == pytest.approx(expected, rel=1e-12, abs=0)
    assert r.diagnostics["equilibrium"][1]["relative_force_error"] < 1e-12
