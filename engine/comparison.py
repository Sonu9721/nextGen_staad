"""Quantitative reference comparison; deviations and ties are never hidden."""

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class Tolerances:
    relative: float = 1e-3
    force: float = 0.01
    moment: float = 0.01
    displacement: float = 0.05

    def limit(self, path, value):
        absolute = (
            self.displacement
            if "displacement" in path or ".df." in path
            else self.moment
            if "bending_moment" in path or ".bm." in path
            else self.force
        )
        return max(absolute, self.relative * abs(value))


def metrics(data, path=""):
    if not isinstance(data, dict):
        return
    if isinstance(data.get("value"), (float, int)):
        yield path, data
    for key, value in data.items():
        if key in {"analysis", "units", "global"}:
            continue
        if isinstance(value, dict):
            yield from metrics(value, f"{path}.{key}".strip("."))


def reference_point_value(path, point, solver, source_length):
    cid = point.get("load_case", point.get("governing_load_case"))
    if cid not in solver.cases:
        return None
    case = solver.cases[cid]
    mid = point.get("member_id")
    node = point.get("node_id")
    displacement = "displacement" in path or ".df." in path
    signed = path.startswith("casement.") and not displacement
    if displacement and node in solver.node_indices:
        i = 6 * solver.node_indices[node]
        return float(np.linalg.norm(case.displacement[i : i + 3]) * 1000)
    if mid not in case.members:
        return None
    member = case.members[mid]
    location = point.get("location")
    axis = point.get("axis") or (
        "MY"
        if "bending_moment.minor" in path
        else "MZ"
        if "bending_moment" in path or ".bm." in path
        else "FZ"
        if "shear_force.minor" in path
        else "FY"
        if "shear_force" in path or ".sf." in path
        else "FX"
    )
    if location in {"start", "end"}:
        end = 0 if location == "start" else 1
        index = ("FX", "FY", "FZ", "MX", "MY", "MZ").index(axis)
        value = float(member.end_forces[6 * end + index] / 1000)
    else:
        from .units import LENGTH_M, ALIASES

        unit = point.get("station_unit", source_length).upper()
        unit = ALIASES.get(unit, unit)
        x = float(point.get("station", 0)) * LENGTH_M[unit]
        x = min(max(0, x), member.element.length)
        if displacement:
            value = float(
                np.linalg.norm(
                    member.translation(
                        x, relative=path.startswith("casement."), legacy_section=True
                    )
                )
                * 1000
            )
            if "properties.transom." in path:
                m = member.element.member
                end_values = [
                    np.linalg.norm(
                        case.displacement[
                            6 * solver.node_indices[n] : 6 * solver.node_indices[n] + 3
                        ]
                    )
                    * 1000
                    for n in (m.start, m.end)
                ]
                value -= min(end_values)
        else:
            index = ("FX", "FY", "FZ", "MX", "MY", "MZ").index(axis)
            value = float(member.internal(x)[index] / 1000)
    return value if signed else abs(value)


def compare(reference, candidate, solver=None, tolerances=None):
    reference = reference.get("result", reference)
    candidate = candidate.get("result", candidate)
    tolerance = tolerances or Tolerances()
    actual = dict(metrics(candidate))
    rows = []
    length = reference.get("units", {}).get("staad_detected", {}).get("length", "METER")
    for path, expected in metrics(reference):
        row = {
            "metric": path,
            "reference": expected["value"],
            "candidate": None,
            "value_status": "missing",
            "location_status": "unverified",
        }
        if path in actual:
            got = actual[path]
            value = got["value"]
            limit = tolerance.limit(path, expected["value"])
            difference = value - expected["value"]
            row.update(
                candidate=value,
                difference=difference,
                absolute_difference=abs(difference),
                tolerance=limit,
                value_status="pass" if abs(difference) <= limit else "difference",
                reference_location={
                    k: v
                    for k, v in expected.items()
                    if k
                    in {
                        "member_id",
                        "node_id",
                        "station",
                        "station_unit",
                        "load_case",
                        "location",
                    }
                },
                candidate_location={
                    k: v
                    for k, v in got.items()
                    if k
                    in {
                        "member_id",
                        "node_id",
                        "station",
                        "station_unit",
                        "load_case",
                        "location",
                    }
                },
            )
            keys = ("member_id", "node_id", "load_case", "location")
            same = all(expected.get(k) == got.get(k) for k in keys)
            row["location_status"] = "same_ids" if same else "different_ids"
            if solver is not None:
                evaluated = reference_point_value(path, expected, solver, length)
                row["candidate_at_reference_point"] = evaluated
                if evaluated is not None:
                    if abs(evaluated - value) <= limit:
                        row["location_status"] = "equivalent_within_tolerance"
                    elif ("bending_moment" in path or ".bm." in path) and abs(
                        evaluated - expected["value"]
                    ) <= limit:
                        row["location_status"] = (
                            "reference_station_reproduced_true_extremum_differs"
                        )
                    else:
                        row["location_status"] = "requires_review"
        rows.append(row)
    return {
        "metrics": rows,
        "counts": {
            key: sum(r["value_status"] == key for r in rows)
            for key in ("pass", "difference", "missing")
        },
        "all_values_within_tolerance": all(r["value_status"] == "pass" for r in rows),
    }
