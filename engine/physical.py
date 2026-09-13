"""Continuous physical envelopes, separate from the legacy extraction contract.

All fields are piecewise polynomials in a normalized interval coordinate. Their
derivative roots locate extrema without a station grid. Load jumps are evaluated
on both sides. Translations include axial, bending and shear deformation.
"""

from math import factorial
import numpy as np
from numpy.polynomial import Polynomial as P
from .errors import check_cancel


def real_roots(poly):
    coefficients = np.asarray(poly.coef)
    scale = float(np.max(np.abs(coefficients)))
    if scale == 0:
        return []
    normalized = P(coefficients / scale).trim(1e-13)
    return [
        float(r.real)
        for r in normalized.roots()
        if abs(r.imag) < 1e-7 and 0 < r.real < 1
    ]


def interval_fields(member, a, b, relative=False):
    x = P([a, b - a])
    cache = getattr(member, "_physical_fields", None)
    if cache is None:
        cache = member._physical_fields = {}
    if (a, b) in cache:
        forces, translations = cache[a, b]
        if relative:
            u, length = member.displacement, member.element.length
            translations = [
                value - u[i] * (1 - x / length) - u[i + 6] * x / length
                for i, value in enumerate(translations)
            ]
        return forces, translations
    probe = (a + b) / 2

    def integral(power, moments=False):
        result = [P([0.0]) for _ in range(3)]
        for load in member.loads:
            if probe < load.a or load.moment != moments:
                continue
            for i in range(3):
                if load.qa[i] == 0 and load.qb[i] == 0:
                    continue
                if load.point:
                    result[i] += load.qa[i] * (x - load.a) ** power / factorial(power)
                    continue
                slope = (load.qb[i] - load.qa[i]) / (load.b - load.a)
                result[i] += (
                    load.qa[i] * (x - load.a) ** (power + 1) / factorial(power + 1)
                )
                result[i] += slope * (x - load.a) ** (power + 2) / factorial(power + 2)
                if probe > load.b:
                    result[i] -= (
                        load.qb[i] * (x - load.b) ** (power + 1) / factorial(power + 1)
                    )
                    result[i] -= (
                        slope * (x - load.b) ** (power + 2) / factorial(power + 2)
                    )
        return result

    p0, p1, p3 = integral(0), integral(1), integral(3)
    c0, c2 = integral(0, moments=True), integral(2, moments=True)
    q, u, e = member.end_forces, member.displacement, member.element
    E, s = e.material.e, e.section
    forces = [
        q[0] + p0[0],
        q[1] + p0[1],
        q[2] + p0[2],
        q[3] + c0[0],
        q[4] + x * q[2] + p1[2] + c0[1],
        q[5] - x * q[1] - p1[1] + c0[2],
    ]
    translations = [
        u[0] + (-q[0] * x - p1[0]) / (E * s.ax),
        u[1]
        + u[5] * x
        + (-q[5] * x**2 / 2 + q[1] * x**3 / 6 + p3[1] - c2[2]) / (E * s.iz)
        - (q[1] * x + p1[1]) * e.shear_flexibility[0],
        u[2]
        - u[4] * x
        + (q[4] * x**2 / 2 + q[2] * x**3 / 6 + p3[2] + c2[1]) / (E * s.iy)
        - (q[2] * x + p1[2]) * e.shear_flexibility[1],
    ]
    cache[a, b] = forces, translations
    if relative:
        translations = [
            value - u[i] * (1 - x / e.length) - u[i + 6] * x / e.length
            for i, value in enumerate(translations)
        ]
    return forces, translations


def intervals(member):
    cuts = sorted(
        {
            0.0,
            member.element.length,
            *[station for load in member.loads for station in (load.a, load.b)],
        }
    )
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]


def displacement_peak(member, relative=False):
    best = (0.0, 0.0)
    for a, b in intervals(member):
        _, translations = interval_fields(member, a, b, relative)
        scale = max(float(np.max(abs(p.coef))) for p in translations)
        if scale == 0:
            continue
        squared = sum(((p / scale) ** 2 for p in translations), P([0.0]))
        for t in [0.0, 1.0, *real_roots(squared.deriv())]:
            magnitude = float(np.linalg.norm([p(t) for p in translations]))
            if magnitude > best[0]:
                best = magnitude, a + t * (b - a)
    return best


def build_physical_response(result, selected_cases, abort_check=None):
    forces = {axis: None for axis in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
    displacements = {"absolute": None, "chord_relative": None}

    def point(value, mid, cid, station, side=None):
        data = dict(
            value=abs(value),
            signed_value=value,
            member_id=mid,
            load_case=cid,
            station=station,
            station_unit="m",
        )
        if side:
            data["station_side"] = side
        return data

    for cid in selected_cases:
        for mid, member in result.cases[cid].members.items():
            check_cancel(abort_check)
            for a, b in intervals(member):
                fields, _ = interval_fields(member, a, b)
                for axis, poly in zip(forces, fields):
                    for t in [0.0, 1.0, *real_roots(poly.deriv())]:
                        value = float(poly(t)) / 1000
                        if forces[axis] is None or abs(value) > forces[axis]["value"]:
                            forces[axis] = point(
                                value,
                                mid,
                                cid,
                                a + t * (b - a),
                                "right" if t == 0 else "left" if t == 1 else "interior",
                            )
            for mode in displacements:
                value, x = displacement_peak(member, relative=mode == "chord_relative")
                value *= 1000
                if displacements[mode] is None or value > displacements[mode]["value"]:
                    displacements[mode] = point(value, mid, cid, x)
        # Nodal translations are also physical results at released connections.
        for node, i in result.node_indices.items():
            value = float(
                np.linalg.norm(result.cases[cid].displacement[6 * i : 6 * i + 3]) * 1000
            )
            if (
                displacements["absolute"] is None
                or value > displacements["absolute"]["value"]
            ):
                displacements["absolute"] = dict(
                    value=value, node_id=node, load_case=cid
                )
    return dict(
        forces=forces,
        displacement=displacements,
        units={"force": "kN", "moment": "kN-m", "displacement": "mm", "station": "m"},
        load_cases=list(selected_cases),
        scope="All members; local force axes; resultant translations including shear; continuous polynomial extrema with both sides of point loads.",
        legacy_contract="properties and casement retain the original extraction definitions and station sampling.",
    )
