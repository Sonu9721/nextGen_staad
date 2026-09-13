"""Independent exact solutions and invariants, not sample-value fitting."""

import numpy as np
import pytest
from engine.parser import parse_text
from engine.solver import solve
from engine.errors import AnalysisError
from engine.element import local_axes, stiffness
from engine.model import Section


def beam(
    *, support="1 FIXED", load="JOINT LOAD\n2 FY -1000", release="", length=4, beta=0
):
    return parse_text(f"""STAAD SPACE
UNIT METER NEWTON
JOINT COORDINATES
1 0 0 0; 2 {length} 0 0
MEMBER INCIDENCES
1 1 2
DEFINE MATERIAL START
ISOTROPIC TEST
E 200000000000
POISSON .3
DENSITY 0
END DEFINE MATERIAL
CONSTANTS
MATERIAL TEST ALL
BETA {beta} ALL
MEMBER PROPERTY AMERICAN
1 PRIS AX .01 IX .00001 IY .00002 IZ .00004
SUPPORTS
{support}
{release}
LOAD 1 TITLE TEST
{load}
LOAD COMB 2 DOUBLE
1 2
PERFORM ANALYSIS
FINISH
""")


def test_cantilever_tip_force():
    result = solve(beam())
    c = result.cases[1]
    m = c.members[1]
    assert c.displacement[7] == pytest.approx(
        -1000 * 4**3 / (3 * 200e9 * 4e-5), rel=1e-11
    )
    assert c.displacement[11] == pytest.approx(-1000 * 4**2 / (2 * 200e9 * 4e-5))
    np.testing.assert_allclose(c.reactions[:6], [0, 1000, 0, 0, 0, 4000], atol=1e-8)
    np.testing.assert_allclose(m.translation(4), c.displacement[6:9], atol=1e-12)


@pytest.mark.parametrize("kind", ["udl", "point"])
def test_simply_supported_interior_deflection(kind):
    load = (
        "MEMBER LOAD\n1 UNI GY -1000"
        if kind == "udl"
        else "MEMBER LOAD\n1 CON GY -1000 2"
    )
    r = solve(beam(support="1 PINNED\n2 FIXED BUT FX MY MZ", load=load))
    m = r.cases[1].members[1]
    expected = (
        5 * 1000 * 4**4 / (384 * 200e9 * 4e-5)
        if kind == "udl"
        else 1000 * 4**3 / (48 * 200e9 * 4e-5)
    )
    assert m.translation(2)[1] == pytest.approx(-expected, rel=1e-11)
    expected_m = 1000 * 4**2 / 8 if kind == "udl" else 1000 * 4 / 4
    assert m.extrema("MZ")[0] == pytest.approx(-expected_m)
    assert m.extrema("MZ")[1] == pytest.approx(2.0)


def test_fixed_fixed_udl_has_span_deflection_at_zero_nodal_motion():
    r = solve(beam(support="1 2 FIXED", load="MEMBER LOAD\n1 UNI GY -1000"))
    m = r.cases[1].members[1]
    np.testing.assert_allclose(r.cases[1].displacement, 0, atol=1e-15)
    assert m.translation(2)[1] == pytest.approx(-1000 * 4**4 / (384 * 200e9 * 4e-5))
    assert m.end_forces[5] == pytest.approx(1000 * 4**2 / 12)


def test_offcenter_point_bending_kink():
    r = solve(
        beam(
            support="1 PINNED\n2 FIXED BUT FX MY MZ",
            load="MEMBER LOAD\n1 CON GY -1000 1.3",
        )
    )
    extrema = r.cases[1].members[1].extrema("MZ")
    assert extrema[1] == pytest.approx(1.3)
    assert extrema[0] == pytest.approx(-1000 * 1.3 * 2.7 / 4)


def test_axial_bar_and_combination():
    r = solve(beam(load="JOINT LOAD\n2 FX 1000"))
    assert r.cases[1].displacement[6] == pytest.approx(1000 * 4 / (200e9 * 0.01))
    np.testing.assert_allclose(r.cases[2].displacement, 2 * r.cases[1].displacement)
    np.testing.assert_allclose(
        r.cases[2].members[1].end_forces, 2 * r.cases[1].members[1].end_forces
    )


def test_released_end_recovers_rotation_and_load():
    r = solve(
        beam(
            support="1 2 FIXED",
            release="MEMBER RELEASE\n1 END MZ",
            load="MEMBER LOAD\n1 UNI GY -1000",
        )
    )
    m = r.cases[1].members[1]
    assert m.end_forces[11] == 0
    assert m.end_forces[5] == pytest.approx(1000 * 4**2 / 8)
    assert m.end_forces[1] == pytest.approx(5 * 1000 * 4 / 8)
    assert m.displacement[11] != 0
    assert m.translation(4)[1] == pytest.approx(0.0, abs=1e-12)


def test_axial_start_release():
    r = solve(
        beam(
            support="1 2 FIXED",
            release="MEMBER RELEASE\n1 START FX",
            load="MEMBER LOAD\n1 UNI GX 100",
        )
    )
    m = r.cases[1].members[1]
    assert m.end_forces[0] == 0
    assert m.end_forces[6] == pytest.approx(-400)
    assert m.displacement[0] == pytest.approx(100 * 4**2 / (2 * 200e9 * 0.01))


def test_beta_rotates_major_bending_to_global_z():
    r = solve(beam(beta=90, load="JOINT LOAD\n2 FZ -1000"))
    assert r.cases[1].displacement[8] == pytest.approx(
        -1000 * 4**3 / (3 * 200e9 * 4e-5)
    )
    np.testing.assert_allclose(
        local_axes(np.array([0.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), 90),
        [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
        atol=1e-15,
    )


def test_spatial_rotation_and_stiffness_symmetry():
    r = local_axes(np.zeros(3), np.array([2.0, 3.0, 4.0]), 27)
    np.testing.assert_allclose(r @ r.T, np.eye(3), atol=1e-14)
    assert np.linalg.det(r) == pytest.approx(1.0)
    k = stiffness(3, 200e9, 80e9, Section(0.01, 1e-5, 2e-5, 4e-5))
    np.testing.assert_allclose(k, k.T)
    assert sum(np.linalg.eigvalsh(k) < 1e-6) == 6


def test_mechanism_is_rejected():
    with pytest.raises(AnalysisError, match="mechanism"):
        solve(beam(support="1 PINNED"))


def test_mixed_units_semicolon_continuation(tmp_path):
    model = beam()
    text = """STAAD SPACE
UNIT CM NEWTON
JOINT COORDINATES
1 0 0 0; 2 400 0 0
MEMBER INCIDENCES
1 1 2
DEFINE MATERIAL START
ISOTROPIC TEST
E 20000000
POISSON .3
END DEFINE MATERIAL
CONSTANTS
MATERIAL TEST ALL
MEMBER PROPERTY AMERICAN
1 PRIS AX 100 IX 1000 -
IY 2000 IZ 4000
SUPPORTS
1 FIXED
UNIT KN METER
LOAD 1 TITLE TEST
JOINT LOAD
2 FY -1
PERFORM ANALYSIS
FINISH
"""
    mixed = parse_text(text)
    np.testing.assert_allclose(
        solve(mixed).cases[1].displacement,
        solve(model).cases[1].displacement,
        rtol=1e-12,
        atol=1e-14,
    )


@pytest.mark.parametrize(
    "replacement",
    [
        "PDELTA ANALYSIS",
        "PERFORM BUCKLING ANALYSIS",
        "PERFORM ANALYSIS PRINT STATICS CHECK",
    ],
)
def test_unsupported_analysis_does_not_silently_solve(replacement):
    with pytest.raises(AnalysisError) as e:
        parse_text("STAAD SPACE\nUNIT METER KN\n" + replacement + "\nFINISH")
    assert e.value.code == "UNSUPPORTED_STAAD_COMMAND"


def test_cancellation():
    with pytest.raises(AnalysisError) as e:
        solve(beam(), abort_check=lambda: True)
    assert e.value.code == "ANALYSIS_CANCELLED"
