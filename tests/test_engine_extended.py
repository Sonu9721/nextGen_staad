from pathlib import Path
import numpy as np
import pytest
from engine.errors import AnalysisError
from engine.parser import parse_text, parse_std
from engine.model import StructuralModel, Member, Section, Material, LoadCase, FloorLoad
from engine.solver import solve
from engine.loads import panel_loads, LocalLoad
from engine.element import FrameElement
from test_engine import beam


def test_timoshenko_cantilever_and_loaded_span():
    model = beam()
    model.members[1].section.ay = 0.005
    r = solve(model)
    G = 200e9 / 2.6
    expected = 1000 * 4**3 / (3 * 200e9 * 4e-5) + 1000 * 4 / (G * 0.005)
    assert r.cases[1].displacement[7] == pytest.approx(-expected)
    assert r.cases[1].members[1].translation(4)[1] == pytest.approx(-expected)
    model = beam(
        support="1 PINNED\n2 FIXED BUT FX MY MZ", load="MEMBER LOAD\n1 UNI GY -1000"
    )
    model.members[1].section.ay = 0.005
    m = solve(model).cases[1].members[1]
    flex = 5 * 1000 * 4**4 / (384 * 200e9 * 4e-5)
    shear = 1000 * 4**2 / (8 * G * 0.005)
    assert m.translation(2)[1] == pytest.approx(-flex - shear)
    assert m.translation(2, legacy_section=True)[1] == pytest.approx(-flex)


def test_torsion():
    r = solve(beam(load="JOINT LOAD\n2 MX 1000"))
    assert r.cases[1].displacement[9] == pytest.approx(
        1000 * 4 / ((200e9 / 2.6) * 1e-5)
    )


def test_portal_hinged_crossbeam_has_cantilever_sway():
    model = StructuralModel()
    model.nodes = {
        1: np.array([0.0, 0.0, 0.0]),
        2: np.array([0.0, 3.0, 0.0]),
        3: np.array([4.0, 3.0, 0.0]),
        4: np.array([4.0, 0.0, 0.0]),
    }
    model.materials = {"S": Material("S", 200e9, 0.3, 0.0)}
    for mid, a, b in [(1, 1, 2), (2, 2, 3), (3, 4, 3)]:
        model.members[mid] = Member(mid, a, b, "S", Section(0.01, 1e-5, 2e-5, 4e-5))
    model.members[2].releases = {5, 11}
    model.supports = {1: set(range(6)), 4: set(range(6))}
    model.cases = {
        1: LoadCase(
            1,
            nodal={
                2: np.array([500.0, 0, 0, 0, 0, 0]),
                3: np.array([500.0, 0, 0, 0, 0, 0]),
            },
        )
    }
    r = solve(model)
    assert r.cases[1].displacement[6] == pytest.approx(500 * 3**3 / (3 * 200e9 * 4e-5))
    assert r.cases[1].displacement[12] == pytest.approx(r.cases[1].displacement[6])
    np.testing.assert_allclose(r.cases[1].members[2].end_forces, 0.0, atol=1e-8)


def panel_model(split=False):
    model = beam(support="1 2 FIXED", load="JOINT LOAD\n2 FZ -1")
    model.nodes = {
        1: np.array([0.0, 0.0, 0.0]),
        2: np.array([4.0, 0.0, 0.0]),
        3: np.array([4.0, 2.0, 0.0]),
        4: np.array([0.0, 2.0, 0.0]),
    }
    connections = [(1, 2), (2, 3), (3, 4), (4, 1)]
    if split:
        model.nodes[5] = np.array([1.0, 0.0, 0.0])
        connections = [(1, 5), (5, 2), (2, 3), (3, 4), (4, 1)]
    model.members = {
        i: Member(i, a, b, "TEST", Section(0.01, 1e-5, 2e-5, 4e-5))
        for i, (a, b) in enumerate(connections, 1)
    }
    model.supports = {n: set(range(6)) for n in model.nodes}
    model.cases = {1: LoadCase(1)}
    return model


@pytest.mark.parametrize("split", [False, True])
def test_floor_distribution_and_split_member_equivalence(split):
    model = panel_model(split)
    floor = FloorLoad("GZ", -1000, {"Z": (-1.0, 1.0)})
    loads, report = panel_loads(model, floor)
    assert report["area_m2"] == pytest.approx(8.0)
    assert report["force_N"] == pytest.approx(-8000.0)
    model.cases[1].floor = [floor]
    r = solve(model)
    assert r.cases[1].reactions.reshape(-1, 6)[:, 2].sum() == pytest.approx(8000.0)
    # Each short side carries a triangular 1 kN/m peak, area 1 kN.
    right = next(mid for mid, m in model.members.items() if m.start == 2 and m.end == 3)
    assert sum(
        (p.qa + p.qb) * (p.b - p.a) / 2 for p in loads if p.member == right
    ) == pytest.approx(-1000.0)


def test_partial_floor_range_rejected():
    with pytest.raises(AnalysisError, match="cuts through"):
        panel_loads(panel_model(), FloorLoad("GZ", -1000, {"X": (0.0, 3.0)}))


def test_nonrectangular_floor_rejected():
    model = panel_model()
    model.nodes[3][0] = 3.0
    with pytest.raises(AnalysisError):
        panel_loads(model, FloorLoad("GZ", -1000, {}))


def test_disconnected_floor_intersection_rejected():
    model = panel_model()
    model.nodes[5] = np.array([1.0, 0.0, 0.0])
    model.members[5] = Member(5, 5, 3, "TEST", Section(0.01, 1e-5, 2e-5, 4e-5))
    with pytest.raises(AnalysisError):
        panel_loads(model, FloorLoad("GZ", -1000, {}))


def test_piecewise_linear_load_integral_and_equivalent_resultant():
    model = beam()
    e = FrameElement(model, model.members[1], {1: 0, 2: 1})
    load = LocalLoad(0, 4, np.array([0.0, 0.0, 0.0]), np.array([0.0, -1200.0, 0.0]))
    np.testing.assert_allclose(load.integral(4), [0, -2400, 0])
    p = e.equivalent_load([load])
    assert p[1] + p[7] == pytest.approx(-2400.0)
    assert p[5] + p[11] + 4 * p[7] == pytest.approx(-6400.0)


def test_imperial_units_agree_with_si():
    si = beam(load="JOINT LOAD\n2 FY -4448.2216152605")
    imperial = beam(load="UNIT INCH KIP\nJOINT LOAD\n2 FY -1")
    np.testing.assert_allclose(
        solve(si).cases[1].displacement, solve(imperial).cases[1].displacement
    )


@pytest.mark.parametrize(
    "text,code",
    [
        ("JOINT COORDINATES\n1 0 0 0; 1 1 0 0", "INVALID_NODE"),
        ("MEMBER OFFSET\n1 START 0 1 0", "UNSUPPORTED_STAAD_COMMAND"),
        ("ELEMENT INCIDENCES SHELL", "UNSUPPORTED_STAAD_COMMAND"),
        ("JOINT COORDINATES\n1 NAN 0 0", "INVALID_STD_FILE"),
        ("UNIT BANANA KN", "INVALID_STD_FILE"),
        ("MEMBER PROPERTY AMERICAN\n1 PRIS AX 1 IY 2 IZ 3", "INVALID_SECTION"),
        ("JOINT COORDINATES\n1 0 0 -", "INVALID_STD_FILE"),
    ],
)
def test_parser_rejects_unsupported_or_invalid_input(text, code):
    with pytest.raises(AnalysisError) as exc:
        parse_text("STAAD SPACE\nUNIT METER KN\n" + text)
    assert exc.value.code == code


def test_load_outside_member_rejected():
    with pytest.raises(AnalysisError, match="outside member"):
        beam(load="MEMBER LOAD\n1 CON GY -1000 4.1")


def test_zero_length_rejected():
    with pytest.raises(AnalysisError) as e:
        beam(length=0)
    assert e.value.code == "ZERO_LENGTH_MEMBER"


def test_reference_fixture_parses_releases_and_mixed_units():
    p = (
        Path(__file__).parents[1]
        / "staad-max-extractor/tests/fixtures/Casement_CF-3T4S-3F-C_STAAD.std"
    )
    m = parse_std(p)
    assert len(m.members) == 70
    assert m.materials["ALUMINUM"].e == pytest.approx(6.89476e10)
    assert m.materials["ALUMINUM"].density == pytest.approx(27101.8)
    assert m.members[1].section.ax == pytest.approx(0.001917)
    assert {4, 5} <= m.members[21].releases
    r = solve(m)
    assert r.diagnostics["relative_residual"] < 1e-9
    for b in r.diagnostics["equilibrium"].values():
        np.testing.assert_allclose(b["force_N"], 0, atol=1e-6)
        np.testing.assert_allclose(b["moment_Nm"], 0, atol=1e-6)
