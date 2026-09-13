"""Piecewise member loading and rectangular, two-way panel tributaries."""

from dataclasses import dataclass
from math import atan2, factorial
import numpy as np
from .model import MemberLoad
from .errors import AnalysisError, check_cancel


@dataclass
class LocalLoad:
    a: float
    b: float
    qa: np.ndarray
    qb: np.ndarray
    point: bool = False
    moment: bool = False

    def integral(self, x, power=0):
        """Integral p(t)*(x-t)^power/power! dt, including point forces."""
        if x < self.a:
            return np.zeros(3)
        if self.point:
            return self.qa * (x - self.a) ** power / factorial(power)
        h = min(x, self.b) - self.a
        if h <= 0:
            return np.zeros(3)
        # Integrating in t-a avoids cancellation of large global coordinates.
        slope = (self.qb - self.qa) / (self.b - self.a)
        X = x - self.a
        from math import comb

        out = np.zeros(3)
        for j in range(power + 1):
            c = comb(power, j) * X ** (power - j) * (-1) ** j / factorial(power)
            out += c * (
                self.qa * h ** (j + 1) / (j + 1) + slope * h ** (j + 2) / (j + 2)
            )
        return out


def load_integral(loads, x, power=0, moments=False):
    return sum(
        (p.integral(x, power) for p in loads if p.moment == moments), np.zeros(3)
    )


def compact_loads(loads):
    """Superpose coincident load segments, preserving exact breakpoint stations."""
    grouped = {}
    for load in loads:
        key = load.a, load.b, load.point, load.moment
        if key not in grouped:
            grouped[key] = LocalLoad(
                load.a, load.b, load.qa.copy(), load.qb.copy(), load.point, load.moment
            )
        else:
            grouped[key].qa += load.qa
            grouped[key].qb += load.qb
    return [p for p in grouped.values() if np.any(p.qa != 0) or np.any(p.qb != 0)]


def panel_loads(model, floor, abort_check=None):
    """Trace bounded planar faces; accept rectangular faces only.

    Collinear side subdivisions stay on the face and receive the corresponding
    part of the tributary load. No hardcoded coordinate grid or member IDs.
    """
    normal = "XYZ".index(floor.direction[-1])
    axes = [i for i in range(3) if i != normal]
    eps = 1e-7
    planes = {}
    for mid, m in model.members.items():
        a, b = model.nodes[m.start], model.nodes[m.end]
        if abs(a[normal] - b[normal]) > eps:
            continue
        normal_range = floor.ranges.get("XYZ"[normal], (-float("inf"), float("inf")))
        if not normal_range[0] - eps <= a[normal] <= normal_range[1] + eps:
            continue
        planes.setdefault(round(float(a[normal]), 7), []).append(mid)
    output, area, face_count = [], 0.0, 0
    for mids in planes.values():
        check_cancel(abort_check)
        neighbors, edge_ids = {}, {}
        coords = {}
        for mid in mids:
            m = model.members[mid]
            for n in (m.start, m.end):
                coords[n] = model.nodes[n][axes]
            edge = tuple(sorted((m.start, m.end)))
            if edge in edge_ids:
                raise AnalysisError(
                    "INVALID_MEMBER", "Overlapping panel boundary members."
                )
            edge_ids[edge] = mid
            neighbors.setdefault(m.start, []).append(m.end)
            neighbors.setdefault(m.end, []).append(m.start)
        # Require physical intersections/subdivisions to be present in incidence.
        for i, mid in enumerate(mids):
            m = model.members[mid]
            a, b = coords[m.start], coords[m.end]
            v = b - a
            varying = np.flatnonzero(abs(v) > eps)
            if len(varying) != 1:
                raise AnalysisError(
                    "UNSUPPORTED_ANALYSIS_FEATURE",
                    "FLOOR LOAD requires axis-aligned rectangular panels.",
                    line=floor.line,
                )
            for n, p in coords.items():
                if n in (m.start, m.end):
                    continue
                frac = np.dot(p - a, v) / np.dot(v, v)
                if -eps <= frac <= 1 + eps and np.linalg.norm(p - a - frac * v) < eps:
                    raise AnalysisError(
                        "INVALID_MEMBER",
                        f"Node {n} lies on unsplit member {mid}; connect panel intersections.",
                    )
            for other in mids[i + 1 :]:
                om = model.members[other]
                if {m.start, m.end} & {om.start, om.end}:
                    continue
                c, d = coords[om.start], coords[om.end]
                w = d - c
                matrix = np.column_stack((v, -w))
                if abs(np.linalg.det(matrix)) > eps:
                    s, t = np.linalg.solve(matrix, c - a)
                    if eps < s < 1 - eps and eps < t < 1 - eps:
                        raise AnalysisError(
                            "INVALID_MEMBER",
                            f"Unconnected crossing between members {mid} and {other}.",
                        )
        for n, adj in neighbors.items():
            adj.sort(key=lambda j: atan2(*(coords[j] - coords[n])[::-1]))
        visited = set()
        for edge in edge_ids:
            for start in (edge, edge[::-1]):
                if start in visited:
                    continue
                cycle = []
                directed = start
                while directed not in visited:
                    visited.add(directed)
                    u, v = directed
                    cycle.append(u)
                    adj = neighbors[v]
                    directed = (v, adj[(adj.index(u) - 1) % len(adj)])
                if directed != start or len(cycle) < 4:
                    continue
                points = np.array([coords[n] for n in cycle])
                # Translation-invariant area avoids catastrophic cancellation at
                # survey coordinates far from the global origin.
                relative_points = points - points[0]
                next_points = np.roll(relative_points, -1, axis=0)
                signed_area = float(
                    np.sum(
                        relative_points[:, 0] * next_points[:, 1]
                        - relative_points[:, 1] * next_points[:, 0]
                    )
                    / 2
                )
                if signed_area <= eps:
                    continue
                lower = points.min(axis=0)
                upper = points.max(axis=0)
                lengths = upper - lower
                bounds = [
                    floor.ranges.get("XYZ"[axis], (-float("inf"), float("inf")))
                    for axis in axes
                ]
                if any(
                    upper[i] <= bounds[i][0] + eps or lower[i] >= bounds[i][1] - eps
                    for i in range(2)
                ):
                    continue
                if any(
                    lower[i] < bounds[i][0] - eps or upper[i] > bounds[i][1] + eps
                    for i in range(2)
                ):
                    raise AnalysisError(
                        "UNSUPPORTED_ANALYSIS_FEATURE",
                        "FLOOR LOAD range cuts through a panel; full panels are required.",
                        line=floor.line,
                    )
                if len(set(cycle)) != len(cycle) or abs(
                    signed_area - float(np.prod(lengths))
                ) > eps * max(1.0, signed_area):
                    raise AnalysisError(
                        "UNSUPPORTED_ANALYSIS_FEATURE",
                        "FLOOR LOAD found a nonrectangular panel.",
                        line=floor.line,
                    )
                for j, n in enumerate(cycle):
                    other = cycle[(j + 1) % len(cycle)]
                    mid = edge_ids[tuple(sorted((n, other)))]
                    m = model.members[mid]
                    a, b = coords[m.start], coords[m.end]
                    axis = int(np.argmax(abs(b - a)))
                    s0 = float(a[axis] - lower[axis])
                    s1 = float(b[axis] - lower[axis])
                    size = float(lengths[axis])
                    cap = float(min(lengths) / 2)
                    cuts = sorted(
                        {
                            s0,
                            s1,
                            *[
                                v
                                for v in (cap, size - cap)
                                if min(s0, s1) < v < max(s0, s1)
                            ],
                        }
                    )
                    for lo, hi in zip(cuts, cuts[1:]):
                        stations = [abs(lo - s0), abs(hi - s0)]
                        values = [
                            floor.pressure * min(lo, size - lo, cap),
                            floor.pressure * min(hi, size - hi, cap),
                        ]
                        if stations[0] > stations[1]:
                            stations.reverse()
                            values.reverse()
                        output.append(
                            MemberLoad(mid, floor.direction, *stations, *values)
                        )
                area += signed_area
                face_count += 1
    if not face_count:
        raise AnalysisError(
            "INVALID_LOAD",
            "FLOOR LOAD did not enclose a supported panel.",
            line=floor.line,
        )
    transferred = sum((p.b - p.a) * (p.qa + p.qb) / 2 for p in output)
    if not np.isclose(transferred, area * floor.pressure, rtol=1e-9, atol=1e-6):
        raise AnalysisError(
            "NUMERICAL_FAILURE",
            "Floor tributary load failed area equilibrium.",
            stage="analysis",
        )
    return output, {
        "panels": face_count,
        "area_m2": area,
        "force_N": transferred,
        "direction": floor.direction,
    }


def build_loads(model, elements, abort_check=None):
    by_case, floors = {}, {}
    for cid, case in model.cases.items():
        check_cancel(abort_check)
        applied = list(case.member)
        reports = []
        for direction, factor in case.selfweight:
            for mid, m in model.members.items():
                q = model.materials[m.material].density * m.section.ax * factor
                applied.append(
                    MemberLoad(mid, "G" + direction, 0.0, elements[mid].length, q, q)
                )
        for floor in case.floor:
            generated, report = panel_loads(model, floor, abort_check)
            applied.extend(generated)
            reports.append(report)
        local = {mid: [] for mid in model.members}
        for p in applied:
            vector = np.zeros(3)
            vector["XYZ".index(p.direction[-1])] = 1.0
            if p.direction.startswith("G"):
                vector = elements[p.member].rotation @ vector
            local[p.member].append(
                LocalLoad(p.a, p.b, p.qa * vector, p.qb * vector, p.point, p.moment)
            )
        by_case[cid] = {mid: compact_loads(loads) for mid, loads in local.items()}
        floors[cid] = reports
    return by_case, floors
