"""12-DOF prismatic space frame element with consistent loads and releases."""

import math
import numpy as np
from .errors import AnalysisError

GAUSS_X, GAUSS_W = np.polynomial.legendre.leggauss(4)


def local_axes(a, b, beta=0.0):
    x = np.asarray(b) - np.asarray(a)
    x = x / np.linalg.norm(x)
    z = np.cross(x, [0.0, 1.0, 0.0])
    if np.linalg.norm(z) < 1e-10:
        y = np.array([-np.sign(x[1]), 0.0, 0.0])
        z = np.cross(x, y)
    else:
        z = z / np.linalg.norm(z)
        y = np.cross(z, x)
    angle = math.radians(beta)
    c, s = math.cos(angle), math.sin(angle)
    return np.array([x, c * y + s * z, -s * y + c * z])


def shear_areas(section, poisson=0.3):
    # PRIS dimensions describe a rectangular section when YB/ZB are absent.
    # Explicit areas take precedence; otherwise use the rectangular energy area.
    # Cowper's rectangular correction uses the supplied structural area AX,
    # not the solid area of a hollow profile's bounding dimensions.
    default = (
        10 * (1 + poisson) / (12 + 11 * poisson) * section.ax
        if section.yd and section.zd
        else 0.0
    )
    return section.ay or default, section.az or default


def stiffness(length, e, g, section):
    l = length
    k = np.zeros((12, 12))
    for pair, value in [([0, 6], e * section.ax / l), ([3, 9], g * section.ix / l)]:
        k[np.ix_(pair, pair)] = value * np.array([[1.0, -1.0], [-1.0, 1.0]])
    ay, az = shear_areas(section, e / (2 * g) - 1)
    for indices, inertia, sign, area in [
        ([1, 5, 7, 11], section.iz, 1.0, ay),
        ([2, 4, 8, 10], section.iy, -1.0, az),
    ]:
        phi = 12 * e * inertia / (g * area * l * l) if area else 0.0
        block = (
            e
            * inertia
            / (l**3 * (1 + phi))
            * np.array(
                [
                    [12, 6 * l, -12, 6 * l],
                    [6 * l, (4 + phi) * l * l, -6 * l, (2 - phi) * l * l],
                    [-12, -6 * l, 12, -6 * l],
                    [6 * l, (2 - phi) * l * l, -6 * l, (4 + phi) * l * l],
                ]
            )
        )
        signs = np.array([1, sign, 1, sign])
        block *= np.outer(signs, signs)
        k[np.ix_(indices, indices)] = block
    return k


class FrameElement:
    def __init__(self, model, member, node_indices):
        self.member = member
        self.length = model.length(member.id)
        self.material = model.materials[member.material]
        self.section = member.section
        ay, az = shear_areas(self.section, self.material.poisson)
        self.shear_flexibility = np.array(
            [1 / (self.material.g * area) if area else 0.0 for area in (ay, az)]
        )
        self.rotation = local_axes(
            model.nodes[member.start], model.nodes[member.end], member.beta
        )
        self.transform = np.kron(np.eye(4), self.rotation)
        self.indices = np.array(
            [
                6 * node_indices[n] + i
                for n in (member.start, member.end)
                for i in range(6)
            ]
        )
        self.k = stiffness(self.length, self.material.e, self.material.g, self.section)
        self.released = sorted(member.releases)
        self.retained = [i for i in range(12) if i not in self.released]
        self.condensed = self.k.copy()
        if self.released:
            rr = self.k[np.ix_(self.released, self.released)]
            try:
                np.linalg.cholesky(rr)
                self.release_map = np.linalg.solve(
                    rr, self.k[np.ix_(self.released, self.retained)]
                )
            except np.linalg.LinAlgError as exc:
                raise AnalysisError(
                    "MECHANISM_DETECTED",
                    f"Member {member.id}: incompatible release set.",
                    stage="analysis",
                ) from exc
            self.condensed = np.zeros((12, 12))
            self.condensed[np.ix_(self.retained, self.retained)] = (
                self.k[np.ix_(self.retained, self.retained)]
                - self.k[np.ix_(self.retained, self.released)] @ self.release_map
            )

    def shape(self, x):
        # Exact homogeneous Timoshenko displacement functions (no shear locking).
        r = x / self.length
        E = self.material.e
        s = self.section
        n = np.zeros((3, 12))
        n[0, [0, 6]] = [1 - r, r]
        n[1, 1] = 1.0
        n[1, 5] = x
        n[1] += (-self.k[5] * x * x / 2 + self.k[1] * x**3 / 6) / (E * s.iz) - self.k[
            1
        ] * x * self.shear_flexibility[0]
        n[2, 2] = 1.0
        n[2, 4] = -x
        n[2] += (self.k[4] * x * x / 2 + self.k[2] * x**3 / 6) / (E * s.iy) - self.k[
            2
        ] * x * self.shear_flexibility[1]
        return n

    def rotation_shape(self, x):
        """Cross-section rotations work-conjugate to concentrated couples.

        These differ from displacement slopes when transverse shear is active.
        """
        n = np.zeros((3, 12))
        r = x / self.length
        n[0, [3, 9]] = [1 - r, r]
        n[1, 4] = 1.0
        n[1] += (-self.k[4] * x - self.k[2] * x * x / 2) / (
            self.material.e * self.section.iy
        )
        n[2, 5] = 1.0
        n[2] += (-self.k[5] * x + self.k[1] * x * x / 2) / (
            self.material.e * self.section.iz
        )
        return n

    def equivalent_load(self, loads):
        p = np.zeros(12)
        for load in loads:
            if load.point:
                shape = (
                    self.rotation_shape(load.a) if load.moment else self.shape(load.a)
                )
                p += shape.T @ load.qa
            else:
                h = (load.b - load.a) / 2
                for s, w in zip(GAUSS_X, GAUSS_W):
                    x = (load.a + load.b) / 2 + h * s
                    force = load.qa + (load.qb - load.qa) * (x - load.a) / (
                        load.b - load.a
                    )
                    p += w * h * (self.shape(x).T @ force)
        return p

    def condense_load(self, p):
        if not self.released:
            return p.copy()
        result = np.zeros(12)
        result[self.retained] = p[self.retained] - self.release_map.T @ p[self.released]
        return result

    def recover(self, global_displacement, p):
        u = self.transform @ global_displacement[self.indices]
        if self.released:
            rr = self.k[np.ix_(self.released, self.released)]
            u[self.released] = (
                np.linalg.solve(rr, p[self.released])
                - self.release_map @ u[self.retained]
            )
        force = self.k @ u - p
        force[self.released] = 0.0
        return u, force
