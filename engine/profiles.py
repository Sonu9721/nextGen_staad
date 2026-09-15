"""Facade roles from canonical topology, independent of PRIS rows and IDs."""

import numpy as np

from .errors import AnalysisError

ROLES = ("mullion", "stack", "head", "sill", "transom")


def classify_unitized(model, generation_request=None):
    explicit = (generation_request or {}).get("profile_member_ids")
    if explicit is not None:
        if not isinstance(explicit, dict) or set(explicit) - set(ROLES):
            raise AnalysisError(
                "INVALID_PROFILE_MAPPING",
                "profile_member_ids must map known facade roles to member ID lists.",
            )
        seen = set()
        groups = {}
        for role, mids in explicit.items():
            if (
                not isinstance(mids, list)
                or any(
                    type(i) is not int or i not in model.members or i in seen
                    for i in mids
                )
                or len(set(mids)) != len(mids)
            ):
                raise AnalysisError(
                    "INVALID_PROFILE_MAPPING",
                    "Profile mapping contains duplicate or unknown members.",
                )
            seen.update(mids)
            if mids:
                groups[role] = sorted(mids)
        if seen != set(model.members):
            raise AnalysisError(
                "INVALID_PROFILE_MAPPING",
                "An explicit profile mapping must cover every member exactly once.",
            )
        return groups, {
            "method": "explicit profile_member_ids",
            "unclassified_members": [],
        }

    eps = 1e-7
    groups = {role: [] for role in ROLES}
    vertical, horizontal, unknown = [], [], []
    stack_levels = []
    heights = [float(p[1]) for p in model.nodes.values()]
    bottom, top = min(heights), max(heights)
    for mid, member in model.members.items():
        a, b = model.nodes[member.start], model.nodes[member.end]
        delta = b - a
        if abs(delta[1]) > eps and np.linalg.norm(delta[[0, 2]]) <= eps:
            vertical.append(mid)
            # In this supported unitized convention, a released axial splice
            # identifies the panel-stack interface. End direction is immaterial.
            for offset, p in ((0, a), (6, b)):
                if (
                    offset in member.releases
                    and {offset + 4, offset + 5} <= member.releases
                    and bottom + eps < p[1] < top - eps
                ):
                    stack_levels.append(float(p[1]))
        elif abs(delta[1]) <= eps:
            horizontal.append(mid)
        else:
            unknown.append(mid)
    groups["mullion"] = vertical
    for mid in horizontal:
        y = float(model.nodes[model.members[mid].start][1])
        role = (
            "sill"
            if abs(y - bottom) <= eps
            else "head"
            if abs(y - top) <= eps
            else "stack"
            if any(abs(y - level) <= eps for level in stack_levels)
            else "transom"
        )
        groups[role].append(mid)
    if unknown:
        raise AnalysisError(
            "INVALID_PROFILE_MAPPING",
            "Inclined members need an explicit profile_member_ids mapping for fully unitized extraction.",
        )
    return {role: sorted(mids) for role, mids in groups.items() if mids}, {
        "method": "canonical geometry and axial/moment-released vertical splice levels",
        "stack_levels_m": sorted(set(stack_levels)),
        "bottom_m": bottom,
        "top_m": top,
        "assumption": "Global Y vertical; one assembly; stack horizontals coincide with released axial splice ends. Supply profile_member_ids for other role conventions.",
        "unclassified_members": unknown,
    }
