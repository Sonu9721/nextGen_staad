from dataclasses import dataclass
import numpy as np
from .loads import load_integral


@dataclass
class MemberResult:
    element: object
    displacement: np.ndarray
    end_forces: np.ndarray
    loads: list

    def internal(self, x, side="right"):
        q = self.end_forces
        p = load_integral(self.loads, x, 0)
        moment = load_integral(self.loads, x, 1)
        couples = load_integral(self.loads, x, moments=True)
        if side == "left":
            p -= sum(
                (
                    load.qa
                    for load in self.loads
                    if not load.moment and load.point and load.a == x
                ),
                np.zeros(3),
            )
            couples -= sum(
                (p.qa for p in self.loads if p.moment and p.point and p.a == x),
                np.zeros(3),
            )
        return np.array(
            [
                q[0] + p[0],
                q[1] + p[1],
                q[2] + p[2],
                q[3] + couples[0],
                q[4] + x * q[2] + moment[2] + couples[1],
                q[5] - x * q[1] - moment[1] + couples[2],
            ]
        )

    def local_displacement(self, x):
        e = self.element
        s = e.section
        E = e.material.e
        u = self.displacement
        q = self.end_forces
        p1 = load_integral(self.loads, x, 1)
        p3 = load_integral(self.loads, x, 3)
        c2 = load_integral(self.loads, x, 2, moments=True)
        return np.array(
            [
                u[0] + (-q[0] * x - p1[0]) / (E * s.ax),
                u[1]
                + u[5] * x
                + (-q[5] * x * x / 2 + q[1] * x**3 / 6 + p3[1] - c2[2]) / (E * s.iz)
                - (q[1] * x + p1[1]) * e.shear_flexibility[0],
                u[2]
                - u[4] * x
                + (q[4] * x * x / 2 + q[2] * x**3 / 6 + p3[2] + c2[1]) / (E * s.iy)
                - (q[2] * x + p1[2]) * e.shear_flexibility[1],
            ]
        )

    def local_rotation(self, x):
        e, q, u = self.element, self.end_forces, self.displacement
        p2 = load_integral(self.loads, x, 2)
        c1 = load_integral(self.loads, x, 1, moments=True)
        return np.array(
            [
                u[3] + (-q[3] * x - c1[0]) / (e.material.g * e.section.ix),
                u[4]
                + (-q[4] * x - q[2] * x * x / 2 - p2[2] - c1[1])
                / (e.material.e * e.section.iy),
                u[5]
                + (-q[5] * x + q[1] * x * x / 2 + p2[1] - c1[2])
                / (e.material.e * e.section.iz),
            ]
        )

    def translation(self, x, relative=False, legacy_section=False):
        local = self.local_displacement(x)
        if legacy_section:
            # Compatibility recovery combines joint motion and flexural span
            # deformation. Bentley KB0112641 motivates this distinction but
            # does not establish this exact interpolation as OpenSTAAD's formula.
            # Retain full Timoshenko translations in the raw solver.
            e = self.element
            l = e.length
            r = x / l
            h = np.array(
                [
                    1 - 3 * r * r + 2 * r**3,
                    l * (r - 2 * r * r + r**3),
                    3 * r * r - 2 * r**3,
                    l * (-r * r + r**3),
                ]
            )
            # This legacy path interpolates joint rotations with Hermite shape
            # functions and adds the fixed-end Euler-Bernoulli load bubble.
            # Exact reference equivalence remains subject to independent checks.
            from .element import GAUSS_X, GAUSS_W

            def eb_shape(s):
                t = s / l
                return np.array(
                    [
                        1 - 3 * t * t + 2 * t**3,
                        l * (t - 2 * t * t + t**3),
                        3 * t * t - 2 * t**3,
                        l * (-t * t + t**3),
                    ]
                )

            if not hasattr(self, "_eb_fixed_loads"):
                py = np.zeros(4)
                pz = np.zeros(4)
                for load in self.loads:
                    quadrature = (
                        [(load.a, 1.0, load.qa)]
                        if load.point
                        else [
                            (
                                (load.a + load.b) / 2 + (load.b - load.a) * s / 2,
                                w * (load.b - load.a) / 2,
                                load.qa + (load.qb - load.qa) * (s + 1) / 2,
                            )
                            for s, w in zip(GAUSS_X, GAUSS_W)
                        ]
                    )
                    for station, w, p in quadrature:
                        if load.moment:
                            t = station / l
                            slope = np.array(
                                [
                                    (-6 * t + 6 * t * t) / l,
                                    1 - 4 * t + 3 * t * t,
                                    (6 * t - 6 * t * t) / l,
                                    -2 * t + 3 * t * t,
                                ]
                            )
                            py += slope * p[2] * w
                            pz -= slope * p[1] * w
                        else:
                            py += eb_shape(station) * p[1] * w
                            pz += eb_shape(station) * p[2] * w
                self._eb_fixed_loads = (py, pz)
            py, pz = self._eb_fixed_loads
            p3 = load_integral(self.loads, x, 3)
            c2 = load_integral(self.loads, x, 2, moments=True)
            local[1] = h @ self.displacement[[1, 5, 7, 11]] + (
                py[1] * x * x / 2 - py[0] * x**3 / 6 + p3[1] - c2[2]
            ) / (e.material.e * e.section.iz)
            local[2] = h @ (self.displacement[[2, 4, 8, 10]] * [1, -1, 1, -1]) + (
                pz[1] * x * x / 2 - pz[0] * x**3 / 6 + p3[2] + c2[1]
            ) / (e.material.e * e.section.iy)
        if relative:
            r = x / self.element.length
            local -= self.displacement[:3] * (1 - r) + self.displacement[6:9] * r
        return self.element.rotation.T @ local

    def extrema(self, axis):
        index = {"MX": 3, "MY": 4, "MZ": 5}[axis]
        l = self.element.length
        breaks = sorted({0.0, l, *[v for p in self.loads for v in (p.a, p.b)]})
        stations = set(breaks)
        # Shear is quadratic within each interval; its roots locate moment extrema.
        shear = 2 if axis == "MY" else 1
        if axis != "MX":
            for a, b in zip(breaks, breaks[1:]):
                if b - a < 1e-12:
                    continue
                probes = np.array([0.211324865405, 0.5, 0.788675134595])
                values = [self.internal(a + r * (b - a))[shear] for r in probes]
                co = np.polynomial.polynomial.polyfit(probes, values, 2)
                scale = max(abs(v) for v in co)
                if scale == 0:
                    continue
                # Root locations must not depend on load magnitude or units.
                co /= scale
                while len(co) > 1 and abs(co[-1]) < 1e-10:
                    co = co[:-1]
                for root in np.polynomial.polynomial.polyroots(co):
                    if abs(root.imag) < 1e-9 and 0 < root.real < 1:
                        stations.add(a + float(root.real) * (b - a))
        # Member endpoints have only the inward section limit. An applied end
        # couple must not create a fictitious peak outside the member span.
        samples = [
            (
                float(self.internal(x, side="left" if x == l else "right")[index]),
                float(x),
            )
            for x in sorted(stations)
        ]
        samples.extend(
            (float(self.internal(p.a, side="left")[index]), float(p.a))
            for p in self.loads
            if p.moment and p.point and 0 < p.a < l
        )
        low = min(samples, key=lambda v: v[0])
        high = max(samples, key=lambda v: v[0])
        return [low[0], low[1], high[0], high[1]]
