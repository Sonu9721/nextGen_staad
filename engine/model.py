from dataclasses import dataclass, field
import numpy as np
from .errors import AnalysisError
from .units import Units

DOFS = ("FX", "FY", "FZ", "MX", "MY", "MZ")


@dataclass
class Material:
    name: str
    e: float = 0.0
    poisson: float = 0.3
    density: float = 0.0  # Weight density, N/m^3.

    @property
    def g(self):
        return self.e / (2 * (1 + self.poisson))


@dataclass
class Section:
    ax: float
    ix: float
    iy: float
    iz: float
    yd: float = 0.0
    zd: float = 0.0
    ay: float = 0.0
    az: float = 0.0


@dataclass
class Member:
    id: int
    start: int
    end: int
    material: str = ""
    section: Section | None = None
    beta: float = 0.0
    releases: set[int] = field(default_factory=set)


@dataclass
class MemberLoad:
    member: int
    direction: str
    a: float
    b: float
    qa: float
    qb: float
    point: bool = False
    moment: bool = False


@dataclass
class FloorLoad:
    direction: str
    pressure: float
    ranges: dict[str, tuple[float, float]]
    line: int = 0


@dataclass
class LoadCase:
    id: int
    title: str = ""
    nodal: dict[int, np.ndarray] = field(default_factory=dict)
    member: list[MemberLoad] = field(default_factory=list)
    selfweight: list[tuple[str, float]] = field(default_factory=list)
    floor: list[FloorLoad] = field(default_factory=list)


@dataclass
class StructuralModel:
    nodes: dict[int, np.ndarray] = field(default_factory=dict)
    members: dict[int, Member] = field(default_factory=dict)
    materials: dict[str, Material] = field(default_factory=dict)
    supports: dict[int, set[int]] = field(default_factory=dict)
    cases: dict[int, LoadCase] = field(default_factory=dict)
    combinations: dict[int, dict[int, float]] = field(default_factory=dict)
    titles: dict[int, str] = field(default_factory=dict)
    file_units: Units = field(default_factory=Units)
    diagnostics: list[str] = field(default_factory=list)

    def validate(self):
        if not self.nodes or not self.members or not self.cases:
            raise AnalysisError(
                "INVALID_STD_FILE",
                "Nodes, members and primary load cases are required.",
            )
        if len(self.nodes) > 1000 or len(self.members) > 2000:
            raise AnalysisError(
                "UNSUPPORTED_ANALYSIS_FEATURE",
                "Dense solver limit: 1000 nodes / 2000 members.",
            )
        used = set()
        for n, coordinates in self.nodes.items():
            if (
                n <= 0
                or np.shape(coordinates) != (3,)
                or not np.all(np.isfinite(coordinates))
            ):
                raise AnalysisError(
                    "INVALID_NODE", f"Node {n} requires three finite coordinates."
                )
        for m in self.members.values():
            if not np.isfinite(m.beta) or any(d not in range(12) for d in m.releases):
                raise AnalysisError(
                    "INVALID_MEMBER",
                    f"Member {m.id} has invalid orientation or releases.",
                )
            if m.start not in self.nodes or m.end not in self.nodes:
                raise AnalysisError(
                    "INVALID_NODE", f"Member {m.id} references a missing node."
                )
            used.update((m.start, m.end))
            if self.length(m.id) <= 1e-9:
                raise AnalysisError(
                    "ZERO_LENGTH_MEMBER", f"Member {m.id} has zero length."
                )
            s = m.section
            if s is None or any(
                not np.isfinite(v) or v <= 0 for v in (s.ax, s.ix, s.iy, s.iz)
            ):
                raise AnalysisError(
                    "INVALID_SECTION",
                    f"Member {m.id} requires positive AX, IX, IY and IZ.",
                )
            if any(v < 0 or not np.isfinite(v) for v in (s.ay, s.az, s.yd, s.zd)):
                raise AnalysisError(
                    "INVALID_SECTION",
                    f"Member {m.id} has invalid shear areas or dimensions.",
                )
            mat = self.materials.get(m.material)
            if (
                mat is None
                or not np.isfinite(mat.e)
                or mat.e <= 0
                or not -1 < mat.poisson < 0.5
                or not np.isfinite(mat.density)
                or mat.density < 0
            ):
                raise AnalysisError(
                    "INVALID_MATERIAL",
                    f"Member {m.id} has invalid or missing material.",
                )
        if used != set(self.nodes):
            raise AnalysisError(
                "INVALID_NODE", f"Unconnected nodes: {sorted(set(self.nodes) - used)}"
            )
        for n in self.supports:
            if n not in self.nodes:
                raise AnalysisError(
                    "INVALID_SUPPORT", f"Support at nonexistent node {n}."
                )
            if any(d not in range(6) for d in self.supports[n]):
                raise AnalysisError(
                    "INVALID_SUPPORT", f"Invalid restraint at node {n}."
                )
        for c in self.cases.values():
            for n, force in c.nodal.items():
                if n not in self.nodes:
                    raise AnalysisError(
                        "INVALID_LOAD", f"Load at nonexistent node {n}."
                    )
                if np.shape(force) != (6,) or not np.all(np.isfinite(force)):
                    raise AnalysisError(
                        "INVALID_LOAD", f"Node {n} requires six finite load components."
                    )
            for direction, factor in c.selfweight:
                if direction not in {"X", "Y", "Z"} or not np.isfinite(factor):
                    raise AnalysisError(
                        "INVALID_LOAD", "Invalid selfweight direction or factor."
                    )
            for floor in c.floor:
                if floor.direction not in {"GX", "GY", "GZ"} or not np.isfinite(
                    floor.pressure
                ):
                    raise AnalysisError(
                        "INVALID_LOAD", "Invalid floor load direction or pressure."
                    )
                if any(
                    axis not in {"X", "Y", "Z"}
                    or len(bounds) != 2
                    or not np.all(np.isfinite(bounds))
                    or bounds[0] > bounds[1]
                    for axis, bounds in floor.ranges.items()
                ):
                    raise AnalysisError("INVALID_LOAD", "Invalid floor load range.")
            for p in c.member:
                if p.moment and not p.point:
                    raise AnalysisError(
                        "INVALID_LOAD",
                        "Only concentrated member moments are supported.",
                    )
                if p.member not in self.members:
                    raise AnalysisError(
                        "INVALID_LOAD", f"Load on nonexistent member {p.member}."
                    )
                if p.direction not in {"X", "Y", "Z", "GX", "GY", "GZ"} or not np.all(
                    np.isfinite([p.a, p.b, p.qa, p.qb])
                ):
                    raise AnalysisError(
                        "INVALID_LOAD",
                        f"Member {p.member} requires finite loads and valid direction.",
                    )
                length = self.length(p.member)
                if p.b == -1.0 and not p.point:
                    p.b = length
                if p.point and p.a != p.b:
                    raise AnalysisError(
                        "INVALID_LOAD", "A point load requires a single station."
                    )
                if not (0 <= p.a <= p.b <= length + 1e-8) or (
                    not p.point and p.a == p.b
                ):
                    raise AnalysisError(
                        "INVALID_LOAD",
                        f"Load lies outside member {p.member} (length {length:g} m).",
                    )
                p.b = min(length, p.b)
                p.a = min(length, p.a)
        for c, factors in self.combinations.items():
            if (
                c in self.cases
                or not factors
                or any(
                    k not in self.cases or not np.isfinite(v)
                    for k, v in factors.items()
                )
            ):
                raise AnalysisError(
                    "INVALID_LOAD",
                    f"Combination {c} must reference primary cases only.",
                )

    def length(self, mid):
        m = self.members[mid]
        return float(np.linalg.norm(self.nodes[m.end] - self.nodes[m.start]))
