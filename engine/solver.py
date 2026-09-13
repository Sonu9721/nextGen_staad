from dataclasses import dataclass
import time
import numpy as np
from .element import FrameElement
from .loads import build_loads, LocalLoad, compact_loads
from .recovery import MemberResult
from .errors import AnalysisError, check_cancel


@dataclass
class CaseResult:
    displacement: np.ndarray
    reactions: np.ndarray
    applied: np.ndarray
    members: dict[int, MemberResult]


@dataclass
class SolverResult:
    model: object
    node_indices: dict[int, int]
    elements: dict[int, FrameElement]
    cases: dict[int, CaseResult]
    diagnostics: dict


def solve(model, abort_check=None, progress=None):
    started = time.perf_counter()
    model.validate()
    check_cancel(abort_check)

    def report(p, m):
        check_cancel(abort_check)
        if progress:
            progress(p, m)

    node_indices = {n: i for i, n in enumerate(sorted(model.nodes))}
    size = 6 * len(node_indices)
    elements = {}
    K = np.zeros((size, size))
    original_diagonal = np.zeros(size)
    report(30, "Assembling frame stiffness and member releases.")
    for mid, member in model.members.items():
        check_cancel(abort_check)
        e = elements[mid] = FrameElement(model, member, node_indices)
        K[np.ix_(e.indices, e.indices)] += e.transform.T @ e.condensed @ e.transform
        original_diagonal[e.indices] += np.diag(e.transform.T @ e.k @ e.transform)
    report(40, "Generating member loads and panel tributaries.")
    loads, floor_reports = build_loads(model, elements, abort_check)
    primary = list(model.cases)
    F = np.zeros((size, len(primary)))
    member_p = {}
    for j, cid in enumerate(primary):
        check_cancel(abort_check)
        member_p[cid] = {}
        for n, force in model.cases[cid].nodal.items():
            F[6 * node_indices[n] : 6 * node_indices[n] + 6, j] += force
        for mid, e in elements.items():
            p = member_p[cid][mid] = e.equivalent_load(loads[cid][mid])
            F[e.indices, j] += e.transform.T @ e.condense_load(p)
    if not np.all(np.isfinite(K)) or not np.all(np.isfinite(F)):
        raise AnalysisError(
            "NUMERICAL_FAILURE",
            "Stiffness or loading exceeds finite numerical range.",
            stage="analysis",
        )
    restrained = {
        6 * node_indices[n] + d for n, ds in model.supports.items() for d in ds
    }
    free = np.array([i for i in range(size) if i not in restrained], dtype=int)
    # Released node rotations with no connected stiffness are not structural mechanisms
    # if completely unloaded. Never remove zero-stiffness translations or loaded DOFs.
    diag = np.diag(K)
    zero = [
        i for i in free if diag[i] <= 128 * np.finfo(float).eps * original_diagonal[i]
    ]
    ignored = []
    for i in zero:
        if i % 6 < 3 or np.any(F[i] != 0):
            raise AnalysisError(
                "MECHANISM_DETECTED",
                f"Unrestrained or loaded zero-stiffness DOF at node {sorted(node_indices)[i // 6]}, {('UX', 'UY', 'UZ', 'RX', 'RY', 'RZ')[i % 6]}.",
                stage="analysis",
            )
        ignored.append(int(i))
    free = np.array([i for i in free if i not in ignored], dtype=int)
    U = np.zeros_like(F)
    min_eigenvalue = 1.0
    condition = 1.0
    report(50, "Solving primary load cases with a shared stiffness matrix.")
    if len(free):
        scale = 1 / np.sqrt(diag[free])
        A = K[np.ix_(free, free)] * np.outer(scale, scale)
        A = (A + A.T) / 2
        eigen = np.linalg.eigvalsh(A)
        min_eigenvalue = float(eigen[0])
        condition = float(eigen[-1] / eigen[0]) if eigen[0] > 0 else float("inf")
        if min_eigenvalue <= 1e-12:
            _, modes = np.linalg.eigh(A)
            movement = np.zeros(size)
            movement[free] = abs(scale * modes[:, 0])
            length_scale = float(
                np.linalg.norm(np.ptp(list(model.nodes.values()), axis=0))
            )
            movement.reshape(-1, 6)[:, 3:] *= length_scale
            dominant = np.flatnonzero(movement >= 0.25 * np.max(movement))
            descriptions = []
            nodes = sorted(node_indices)
            for d, label in enumerate(("UX", "UY", "UZ", "RX", "RY", "RZ")):
                affected = [nodes[i // 6] for i in dominant if i % 6 == d]
                if affected:
                    descriptions.append(
                        f"{label} at nodes {', '.join(map(str, affected))}"
                    )
            detail = "; ".join(descriptions)
            raise AnalysisError(
                "SINGULAR_MATRIX",
                f"The structure has a mechanism or excessive ill-conditioning (scaled minimum eigenvalue {min_eigenvalue:.3g}). Dominant movement: {detail}. Check the intended supports and member releases; no artificial restraints were added.",
                stage="analysis",
            )
        try:
            U[free] = scale[:, None] * np.linalg.solve(A, scale[:, None] * F[free])
        except np.linalg.LinAlgError as exc:
            raise AnalysisError(
                "NUMERICAL_FAILURE", "Stiffness solution failed.", stage="analysis"
            ) from exc
    check_cancel(abort_check)
    if not np.all(np.isfinite(U)):
        raise AnalysisError(
            "NUMERICAL_FAILURE", "Nonfinite displacement.", stage="analysis"
        )
    residual = K @ U - F
    denominator = np.abs(K) @ np.abs(U) + np.abs(F)
    backward_errors = np.divide(
        np.abs(residual),
        denominator,
        out=np.zeros_like(residual),
        where=denominator > 0,
    )
    case_backward_errors = {
        cid: float(np.max(backward_errors[free, j])) if len(free) else 0.0
        for j, cid in enumerate(primary)
    }
    scaled_backward_errors = {}
    for j, cid in enumerate(primary):
        if len(free):
            norm_residual = float(np.max(np.abs(scale * residual[free, j])))
            norm_rhs = float(np.max(np.abs(scale * F[free, j])))
            norm_solution = float(np.max(np.abs(U[free, j] / scale)))
            norm_bound = float(np.linalg.norm(A, ord=np.inf)) * norm_solution + norm_rhs
            scaled_backward_errors[cid] = (
                norm_residual / norm_bound if norm_bound else 0.0
            )
        else:
            scaled_backward_errors[cid] = 0.0
    relative_residual = (
        float(np.max(abs(residual[free])) / max(1.0, np.max(abs(F))))
        if len(free)
        else 0.0
    )
    if max(scaled_backward_errors.values()) > 1e-10:
        raise AnalysisError(
            "NUMERICAL_FAILURE",
            f"Scaled backward solution error {max(scaled_backward_errors.values()):g} exceeds tolerance.",
            stage="analysis",
        )
    result = SolverResult(model, node_indices, elements, {}, {})
    for j, cid in enumerate(primary):
        report(
            60 + int(15 * j / len(primary)),
            f"Recovering forces and displacements for load case {cid}.",
        )
        members = {}
        for mid, e in elements.items():
            u, q = e.recover(U[:, j], member_p[cid][mid])
            members[mid] = MemberResult(e, u, q, loads[cid][mid])
            mismatch = np.max(abs(members[mid].local_displacement(e.length) - u[6:9]))
            mismatch = max(
                mismatch, np.max(abs(members[mid].local_rotation(e.length) - u[9:12]))
            )
            if mismatch > 1e-7 * max(1.0, np.max(abs(u))):
                raise AnalysisError(
                    "RESULT_RECOVERY_FAILED",
                    f"Member {mid} endpoint compatibility residual {mismatch:g}.",
                    stage="result_extraction",
                )
        reactions = np.zeros(size)
        support_dofs = sorted(restrained)
        reactions[support_dofs] = residual[support_dofs, j]
        result.cases[cid] = CaseResult(
            U[:, j].copy(), reactions, F[:, j].copy(), members
        )
    for cid, factors in model.combinations.items():
        check_cancel(abort_check)
        sources = [(f, result.cases[c]) for c, f in factors.items()]
        members = {}
        for mid, e in elements.items():
            ml = [
                LocalLoad(p.a, p.b, p.qa * f, p.qb * f, p.point, p.moment)
                for f, c in sources
                for p in c.members[mid].loads
            ]
            members[mid] = MemberResult(
                e,
                sum(
                    (f * c.members[mid].displacement for f, c in sources), np.zeros(12)
                ),
                sum((f * c.members[mid].end_forces for f, c in sources), np.zeros(12)),
                compact_loads(ml),
            )
        result.cases[cid] = CaseResult(
            *(
                sum((f * getattr(c, attr) for f, c in sources), np.zeros(size))
                for attr in ("displacement", "reactions", "applied")
            ),
            members,
        )
    balances = {}
    origin = np.mean(list(model.nodes.values()), axis=0)
    balance_length = float(np.linalg.norm(np.ptp(list(model.nodes.values()), axis=0)))
    for cid, c in result.cases.items():
        total_force = np.zeros(3)
        total_moment = np.zeros(3)
        force_scale, moment_scale = 0.0, 0.0

        def accumulate(force, moment, position):
            nonlocal total_force, total_moment, force_scale, moment_scale
            torque = moment + np.cross(position - origin, force)
            total_force += force
            total_moment += torque
            force_scale += float(np.linalg.norm(force))
            moment_scale += float(
                np.linalg.norm(moment)
                + np.linalg.norm(position - origin) * np.linalg.norm(force)
            )

        factors = model.combinations.get(cid, {cid: 1.0})
        for source, factor in factors.items():
            for n, load in model.cases[source].nodal.items():
                accumulate(factor * load[:3], factor * load[3:], model.nodes[n])
        # Independently integrate original physical loads, not the same assembled
        # right-hand side used by the solve. Only actual support reactions count.
        for mr in c.members.values():
            e = mr.element
            for p in mr.loads:
                if p.moment:
                    accumulate(
                        np.zeros(3),
                        e.rotation.T @ p.integral(e.length),
                        model.nodes[e.member.start],
                    )
                    continue
                force = p.integral(e.length)
                first_moment = e.length * force - p.integral(e.length, 1)
                accumulate(
                    e.rotation.T @ force,
                    e.rotation.T @ np.cross([1.0, 0.0, 0.0], first_moment),
                    model.nodes[e.member.start],
                )
        for n, i in node_indices.items():
            reaction = c.reactions[6 * i : 6 * i + 6]
            accumulate(reaction[:3], reaction[3:], model.nodes[n])
        # A pure couple has zero physical force, so roundoff force residuals need
        # a dimensional load scale M/L, not division by another roundoff value.
        # This also handles pure axial loads with zero resultant moment.
        force_scale, moment_scale = (
            max(force_scale, moment_scale / balance_length),
            max(moment_scale, force_scale * balance_length),
        )
        if np.linalg.norm(total_force) > 1e-7 * max(
            force_scale, np.finfo(float).tiny
        ) or np.linalg.norm(total_moment) > 1e-7 * max(
            moment_scale, np.finfo(float).tiny
        ):
            raise AnalysisError(
                "NUMERICAL_FAILURE",
                f"Load case {cid} failed independent physical force/moment balance.",
                stage="analysis",
            )
        balances[cid] = {
            "force_N": total_force.tolist(),
            "moment_Nm": total_moment.tolist(),
            "relative_force_error": float(np.linalg.norm(total_force) / force_scale)
            if force_scale
            else 0.0,
            "relative_moment_error": float(np.linalg.norm(total_moment) / moment_scale)
            if moment_scale
            else 0.0,
        }
    result.diagnostics = {
        "nodes": len(model.nodes),
        "members": len(elements),
        "active_dofs": len(free),
        "ignored_unloaded_rotations": ignored,
        "scaled_condition_number": condition,
        "minimum_scaled_eigenvalue": min_eigenvalue,
        "relative_residual": relative_residual,
        "componentwise_backward_error_by_case": case_backward_errors,
        "scaled_backward_error_by_case": scaled_backward_errors,
        "equilibrium": balances,
        "equilibrium_normalization_length_m": balance_length,
        "floor_loads": floor_reports,
        "solve_seconds": time.perf_counter() - started,
        "element_theory": "Timoshenko (Euler-Bernoulli when shear areas are absent)",
        "shear_area_rule": "Explicit AY/AZ; otherwise Cowper rectangular coefficient times AX when YD and ZD are supplied; no shear when neither is supplied.",
    }
    report(78, "Structural solution complete; building result envelopes.")
    return result
