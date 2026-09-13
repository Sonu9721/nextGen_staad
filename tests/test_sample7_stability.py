"""The original Sample 7 is attached unchanged; its loaded mechanism must fail."""

from pathlib import Path
import numpy as np
import pytest
from engine.element import FrameElement
from engine.loads import build_loads
from engine.parser import parse_std
from engine.solver import solve
from engine.errors import AnalysisError

ROOT = Path(__file__).parents[1]


def test_original_sample7_supports_have_a_loaded_vertical_rigid_mode():
    m = parse_std(ROOT / "examples/sample7/sample_7.std")
    assert len(m.nodes) == 48 and len(m.members) == 68
    assert set(m.cases) == {1, 2, 5} and set(m.combinations) == {3, 4, 6}
    assert sum(p.moment for c in m.cases.values() for p in c.member) == 48
    # Translate every node in the lower assembly vertically by one metre.
    # It has no vertical supports, and axial releases disconnect it above.
    lower = {n for n, p in m.nodes.items() if p[1] <= 5.8}
    assert len(lower) == 20
    assert not any(1 in m.supports.get(n, set()) for n in lower)
    ni = {n: i for i, n in enumerate(sorted(m.nodes))}
    mode = np.zeros(6 * len(ni))
    for n in lower:
        mode[6 * ni[n] + 1] = 1
    elements = {mid: FrameElement(m, member, ni) for mid, member in m.members.items()}
    loads, _ = build_loads(m, elements)
    work = 0.0
    for mid, e in elements.items():
        movement = e.transform @ mode[e.indices]
        np.testing.assert_allclose(e.condensed @ movement, 0, atol=1e-8)
        work += movement @ e.condense_load(e.equivalent_load(loads[1][mid]))
    assert work == pytest.approx(-11994.59676816, abs=1e-7)
    with pytest.raises(AnalysisError) as exc:
        solve(m)
    assert exc.value.code == "SINGULAR_MATRIX"
    assert "UY at nodes" in str(exc.value)
    assert "supports and member releases" in str(exc.value)
