"""Independent closed-form, virtual-work and invariance checks.

The reference below is the Green flexibility function of a cantilever, integrated
directly against applied loads; it does not call the element stiffness, load-vector
or recovery implementation. Fixed-end reactions are solved by compatibility.
"""

from copy import deepcopy
import numpy as np
import pytest
from engine.errors import AnalysisError
from engine.element import local_axes
from engine.model import Member, MemberLoad
from engine.physical import displacement_peak, interval_fields, build_physical_response
from engine.solver import solve
from engine.parser import parse_std
from engine.loads import panel_loads
from engine.model import FloorLoad
from test_engine import beam
from test_engine_extended import panel_model


def flexibility_reference(x, length, ei, ga, loads, fixed_end=False):
    def green(x, a):
        lo, hi = min(x, a), max(x, a)
        return lo**2 * (3 * hi - lo) / (6 * ei) + (lo / ga if ga else 0)

    def slope(x, a):
        return a**2 / (2 * ei) if x >= a else (a * x - x**2 / 2) / ei

    samples = []
    roots, weights = np.polynomial.legendre.leggauss(8)
    for load in loads:
        if load.point:
            samples.append((load.a, load.qa))
        else:
            # Split at the evaluation point where the Green function changes.
            cuts = sorted({load.a, load.b, *([x] if load.a < x < load.b else [])})
            for lo, hi in zip(cuts, cuts[1:]):
                for t, w in zip(roots, weights):
                    a = (lo + hi) / 2 + t * (hi - lo) / 2
                    q = load.qa + (load.qb - load.qa) * (a - load.a) / (load.b - load.a)
                    samples.append((a, q * w * (hi - lo) / 2))
    value = sum(p * green(x, a) for a, p in samples)
    if fixed_end:
        tip = sum(p * green(length, a) for a, p in samples)
        rotation = sum(p * slope(length, a) for a, p in samples)
        flexibility = [
            [green(length, length), length**2 / (2 * ei)],
            [length**2 / (2 * ei), length / ei],
        ]
        end_force, end_moment = np.linalg.solve(flexibility, [-tip, -rotation])
        value += end_force * green(x, length) + end_moment * x**2 / (2 * ei)
    return value


@pytest.mark.parametrize("fixed_end", [False, True])
@pytest.mark.parametrize("shear", [False, True])
@pytest.mark.parametrize("axis", ["Y", "Z"])
def test_virtual_work_partial_triangle_point_loads(axis, shear, fixed_end):
    m = beam(support="1 2 FIXED" if fixed_end else "1 FIXED")
    m.cases[1].nodal.clear()
    m.cases[1].member = [
        MemberLoad(1, axis, 0.3, 2.7, -300, -1200),
        MemberLoad(1, axis, 1.17, 1.17, 721, 721, True),
        MemberLoad(1, axis, 2.1, 3.8, 500, 500),
    ]
    s = m.members[1].section
    if shear:
        s.ay, s.az = 0.00013, 0.00019
    r = solve(m).cases[1].members[1]
    index = 1 if axis == "Y" else 2
    ei = m.materials["TEST"].e * (s.iz if axis == "Y" else s.iy)
    ga = m.materials["TEST"].g * (s.ay if axis == "Y" else s.az)
    for x in [0, 0.3, 0.71, 1.17, 1.91, 2.7, 3.8, 4]:
        expected = flexibility_reference(x, 4, ei, ga, m.cases[1].member, fixed_end)
        assert r.translation(x)[index] == pytest.approx(expected, abs=2e-14, rel=2e-10)


def subdivide(model, count):
    split = deepcopy(model)
    split.nodes = {i + 1: np.array([4 * i / count, 0.0, 0.0]) for i in range(count + 1)}
    original = split.members[1]
    split.members = {
        i: Member(i, i, i + 1, original.material, deepcopy(original.section))
        for i in range(1, count + 1)
    }
    split.supports = {1: set(range(6)), count + 1: set(range(6))}
    for cid, case in split.cases.items():
        case.nodal = {}
        case.member = []
        for load in model.cases[cid].member:
            for i in split.members:
                start, end = (i - 1) * 4 / count, i * 4 / count
                if load.point:
                    if start <= load.a < end or (i == count and load.a == end):
                        case.member.append(
                            MemberLoad(
                                i,
                                load.direction,
                                load.a - start,
                                load.a - start,
                                load.qa,
                                load.qa,
                                True,
                            )
                        )
                else:
                    a, b = max(start, load.a), min(end, load.b)
                    if b > a:
                        q = lambda x: (
                            load.qa
                            + (load.qb - load.qa) * (x - load.a) / (load.b - load.a)
                        )
                        case.member.append(
                            MemberLoad(
                                i, load.direction, a - start, b - start, q(a), q(b)
                            )
                        )
    return split


@pytest.mark.parametrize("count", [2, 5, 11])
@pytest.mark.parametrize("shear", [False, True])
def test_subdivision_invariance_of_physical_solution(count, shear):
    m = beam(support="1 2 FIXED")
    m.cases[1].nodal.clear()
    m.cases[1].member = [
        MemberLoad(1, "Y", 0.23, 3.61, -900, 700),
        MemberLoad(1, "Z", 1.17, 1.17, -500, -500, True),
    ]
    if shear:
        m.members[1].section.ay = 0.001
        m.members[1].section.az = 0.002
    full = solve(m).cases[1].members[1]
    split = solve(subdivide(m, count)).cases[1]
    for x in np.linspace(0, 4, 31):
        i = min(int(x / (4 / count)) + 1, count)
        actual = split.members[i].translation(x - (i - 1) * 4 / count)
        np.testing.assert_allclose(actual, full.translation(x), rtol=2e-9, atol=2e-13)


@pytest.mark.parametrize("factor", [1e-18, 1e-9, 1, 1e12])
def test_uniform_material_and_load_scaling_is_not_a_mechanism(factor):
    m = beam()
    expected = solve(m).cases[1].displacement
    m.materials["TEST"].e *= factor
    m.cases[1].nodal[2] *= factor
    np.testing.assert_allclose(
        solve(m).cases[1].displacement, expected, rtol=1e-12, atol=1e-14
    )


@pytest.mark.parametrize(
    "end,beta", [([2.0, 3.0, 4.0], 37), ([0.0, -4.0, 0.0], 90), ([-4.0, 0.0, 0.0], 17)]
)
def test_spatial_covariance_against_local_cantilever(end, beta):
    m = beam(length=float(np.linalg.norm(end)), beta=beta)
    m.nodes[2] = np.array(end)
    rotation = local_axes(m.nodes[1], m.nodes[2], beta)
    local_force = np.array([200.0, -1300.0, 700.0])
    m.cases[1].nodal[2][:3] = rotation.T @ local_force
    r = solve(m)
    l, e, s = m.length(1), m.materials["TEST"].e, m.members[1].section
    expected = [
        local_force[0] * l / (e * s.ax),
        local_force[1] * l**3 / (3 * e * s.iz),
        local_force[2] * l**3 / (3 * e * s.iy),
    ]
    np.testing.assert_allclose(
        rotation @ r.cases[1].displacement[6:9], expected, rtol=1e-10, atol=1e-13
    )


def test_floor_area_invariance_at_large_survey_coordinates():
    m = panel_model(split=True)
    floor = FloorLoad("GZ", -1000, {})
    reference, report = panel_loads(m, floor)
    for n in m.nodes:
        m.nodes[n] += np.array([1e8, -1e8, 7.0])
    actual, shifted = panel_loads(m, floor)
    assert shifted == report
    assert actual == reference


@pytest.mark.parametrize(
    "bad", ["1 UNI GY -1000 0 -1", "1 UNI GY -1000 0 -2", "1 CON GY -1000 -1"]
)
def test_explicit_negative_member_load_station_is_rejected(bad):
    with pytest.raises(AnalysisError):
        beam(load="MEMBER LOAD\n" + bad)


def test_selfweight_does_not_accept_multiaxis_abbreviation():
    with pytest.raises(AnalysisError):
        beam(load="SELFWEIGHT XY -1")


def test_invalid_text_encoding_is_an_input_error(tmp_path):
    path = tmp_path / "invalid.std"
    path.write_bytes(b"STAAD SPACE\n\xff")
    with pytest.raises(AnalysisError) as exc:
        parse_std(path)
    assert exc.value.code == "INVALID_STD_FILE"


@pytest.mark.parametrize("target", ["node", "density", "force", "beta", "combination"])
def test_nonfinite_canonical_input_is_rejected(target):
    m = beam()
    if target == "node":
        m.nodes[2][0] = np.nan
    if target == "density":
        m.materials["TEST"].density = np.nan
    if target == "force":
        m.cases[1].nodal[2][0] = np.nan
    if target == "beta":
        m.members[1].beta = np.nan
    if target == "combination":
        m.combinations[2][1] = np.nan
    with pytest.raises(AnalysisError):
        solve(m)


def test_true_displacement_peak_between_legacy_stations():
    m = beam(
        support="1 PINNED\n2 FIXED BUT FX MY MZ", load="MEMBER LOAD\n1 CON GY -1000 1.3"
    )
    member = solve(m).cases[1].members[1]
    peak, x = displacement_peak(member)
    expected_x = 4 - np.sqrt((4**2 - 1.3**2) / 3)
    assert x == pytest.approx(expected_x, abs=1e-10)
    assert peak == pytest.approx(abs(member.translation(expected_x)[1]), rel=1e-12)
    assert peak > max(
        np.linalg.norm(member.translation(t)) for t in np.linspace(0, 4, 13)
    )


def test_physical_shear_and_axial_envelopes_capture_interior_jumps():
    m = beam(support="1 2 FIXED")
    m.cases[1].nodal.clear()
    m.cases[1].member = [
        MemberLoad(1, axis, x, x, p, p, True)
        for axis in ["X", "Y"]
        for x, p in [(1.0, -1000), (2.0, 2000), (3.0, -1000)]
    ]
    r = solve(m)
    response = build_physical_response(r, [1])
    assert response["forces"]["FX"]["value"] == pytest.approx(1.0)
    assert response["forces"]["FY"]["value"] == pytest.approx(1.0)
    assert abs(r.cases[1].members[1].end_forces[1]) < 1e-8
    assert abs(r.cases[1].members[1].end_forces[0]) < 1e-8


def test_polynomial_fields_equal_recovered_translation():
    m = beam(load="MEMBER LOAD\n1 UNI GY -1300 .27 3.41\n1 CON GZ 731 1.17")
    m.members[1].section.ay = 0.0002
    member = solve(m).cases[1].members[1]
    for a, b in [(0.27, 1.17), (1.17, 3.41), (3.41, 4)]:
        force, translation = interval_fields(member, a, b)
        for t in [0.01, 0.31, 0.8, 0.99]:
            x = a + t * (b - a)
            np.testing.assert_allclose(
                [p(t) for p in force], member.internal(x), atol=1e-9
            )
            np.testing.assert_allclose(
                [p(t) for p in translation], member.local_displacement(x), atol=1e-13
            )
