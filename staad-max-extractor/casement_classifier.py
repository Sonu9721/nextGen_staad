"""Casement STAAD member topology classification.

Isolated from Fully Unitized PRIS-line-order mapping. Classification priority:
1. Optional request-supplied member maps (`casement_profiles` / `casementProfiles`)
2. Geometry (+ optional minority-PRIS fingerprint hint)
"""

from __future__ import annotations

import logging
from statistics import median
from typing import Any

from profile_classifier import PrisFingerprint

LOGGER = logging.getLogger("staad_max_extractor")

CASEMENT_PROFILE_NAMES: tuple[str, ...] = (
    "interlock",
    "central_meeting",
    "fixed_mullion",
    "horizontal",
    "outer",
)

EPS = 1e-6


def _is_equal(a: float, b: float) -> bool:
    return abs(a - b) <= EPS


def _empty_profiles() -> dict[str, list[int]]:
    return {name: [] for name in CASEMENT_PROFILE_NAMES}


def _is_vertical_member(
    member_id: int,
    member_incidences: dict[int, tuple[int, int]],
    node_coordinates: dict[int, tuple[float, float, float]],
) -> bool:
    incidence = member_incidences.get(member_id)
    if incidence is None:
        return False
    start = node_coordinates.get(incidence[0])
    end = node_coordinates.get(incidence[1])
    if start is None or end is None:
        return False

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    abs_x = abs(dx)
    abs_y = abs(dy)
    abs_z = abs(dz)
    horizontal = max(abs_x, abs_z)
    return abs_y > 0.0 and abs_y >= horizontal


def _member_midpoint(
    member_id: int,
    member_incidences: dict[int, tuple[int, int]],
    node_coordinates: dict[int, tuple[float, float, float]],
) -> tuple[float, float, float] | None:
    incidence = member_incidences.get(member_id)
    if incidence is None:
        return None
    start = node_coordinates.get(incidence[0])
    end = node_coordinates.get(incidence[1])
    if start is None or end is None:
        return None
    return (
        (start[0] + end[0]) / 2.0,
        (start[1] + end[1]) / 2.0,
        (start[2] + end[2]) / 2.0,
    )


def _horizontal_member_y(
    member_id: int,
    member_incidences: dict[int, tuple[int, int]],
    node_coordinates: dict[int, tuple[float, float, float]],
) -> float | None:
    incidence = member_incidences.get(member_id)
    if incidence is None:
        return None
    start = node_coordinates.get(incidence[0])
    end = node_coordinates.get(incidence[1])
    if start is None or end is None:
        return None
    if not _is_equal(start[1], end[1]):
        return None
    return start[1]


def _request_profile_map(generation_request: dict[str, Any] | None) -> dict[str, list[int]] | None:
    if generation_request is None:
        return None
    raw = generation_request.get("casement_profiles")
    if raw is None:
        raw = generation_request.get("casementProfiles")
    if not isinstance(raw, dict):
        return None

    profiles = _empty_profiles()
    found_any = False
    for name in CASEMENT_PROFILE_NAMES:
        values = raw.get(name)
        if values is None:
            continue
        if not isinstance(values, list):
            continue
        member_ids: list[int] = []
        for item in values:
            try:
                member_ids.append(int(item))
            except (TypeError, ValueError):
                continue
        if member_ids:
            found_any = True
        profiles[name] = sorted(set(member_ids))
    return profiles if found_any else None


def _validate_and_disjoint(
    profiles: dict[str, list[int]],
    valid_member_ids: set[int],
) -> dict[str, list[int]]:
    """Keep only valid IDs and enforce disjoint assignment (first profile wins)."""
    result = _empty_profiles()
    claimed: set[int] = set()
    for name in CASEMENT_PROFILE_NAMES:
        cleaned: list[int] = []
        for member_id in profiles.get(name, []):
            if member_id not in valid_member_ids:
                LOGGER.warning(
                    "[STAAD][CASEMENT] Ignoring unknown member_id %s in profile %s",
                    member_id,
                    name,
                )
                continue
            if member_id in claimed:
                LOGGER.warning(
                    "[STAAD][CASEMENT] Member %s already assigned; skipping duplicate in %s",
                    member_id,
                    name,
                )
                continue
            claimed.add(member_id)
            cleaned.append(member_id)
        result[name] = sorted(cleaned)
    return result


def _minority_pris_member_ids(
    member_pris: dict[int, PrisFingerprint] | None,
    candidate_ids: list[int],
) -> set[int]:
    """Members whose PRIS fingerprint is less common than the majority among candidates."""
    if not member_pris or not candidate_ids:
        return set()
    fingerprint_to_members: dict[PrisFingerprint, list[int]] = {}
    for member_id in candidate_ids:
        fingerprint = member_pris.get(member_id)
        if fingerprint is None:
            continue
        matched = None
        for existing in fingerprint_to_members:
            if existing.matches(fingerprint):
                matched = existing
                break
        key = matched if matched is not None else fingerprint
        fingerprint_to_members.setdefault(key, []).append(member_id)
    if len(fingerprint_to_members) < 2:
        return set()
    counts = sorted(
        (len(members), members) for members in fingerprint_to_members.values()
    )
    max_count = counts[-1][0]
    minority: set[int] = set()
    for count, members in counts:
        if count < max_count:
            minority.update(members)
    return minority


def classify_casement_members_from_geometry(
    *,
    member_ids: list[int],
    member_incidences: dict[int, tuple[int, int]] | None,
    node_coordinates: dict[int, tuple[float, float, float]] | None,
    member_pris: dict[int, PrisFingerprint] | None = None,
) -> dict[str, list[int]]:
    incidences = member_incidences or {}
    coordinates = node_coordinates or {}
    if not member_ids or not incidences or not coordinates:
        return _empty_profiles()

    xs = [coords[0] for coords in coordinates.values()]
    ys = [coords[1] for coords in coordinates.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    vertical_ids: list[int] = []
    horizontal_ids: list[int] = []
    for member_id in member_ids:
        if member_id not in incidences:
            continue
        if _is_vertical_member(member_id, incidences, coordinates):
            vertical_ids.append(member_id)
        else:
            horizontal_ids.append(member_id)

    profiles = _empty_profiles()

    outer_verticals: list[int] = []
    interior_verticals: list[int] = []
    for member_id in vertical_ids:
        midpoint = _member_midpoint(member_id, incidences, coordinates)
        if midpoint is None:
            continue
        mid_x = midpoint[0]
        if _is_equal(mid_x, min_x) or _is_equal(mid_x, max_x):
            outer_verticals.append(member_id)
        else:
            interior_verticals.append(member_id)

    outer_horizontals: list[int] = []
    interior_horizontals: list[int] = []
    for member_id in horizontal_ids:
        y_value = _horizontal_member_y(member_id, incidences, coordinates)
        if y_value is None:
            midpoint = _member_midpoint(member_id, incidences, coordinates)
            if midpoint is None:
                continue
            y_value = midpoint[1]
        if _is_equal(y_value, min_y) or _is_equal(y_value, max_y):
            outer_horizontals.append(member_id)
        else:
            interior_horizontals.append(member_id)

    profiles["outer"] = sorted(set(outer_verticals + outer_horizontals))
    profiles["horizontal"] = sorted(set(interior_horizontals))

    interior_transom_ys = [
        y
        for member_id in interior_horizontals
        if (y := _horizontal_member_y(member_id, incidences, coordinates)) is not None
    ]
    if not interior_transom_ys:
        mid_ys = []
        for member_id in interior_verticals:
            midpoint = _member_midpoint(member_id, incidences, coordinates)
            if midpoint is not None:
                mid_ys.append(midpoint[1])
        transom_y = median(mid_ys) if mid_ys else 0.0
    else:
        transom_y = median(interior_transom_ys)

    below_transom: list[int] = []
    above_transom: list[int] = []
    for member_id in interior_verticals:
        midpoint = _member_midpoint(member_id, incidences, coordinates)
        if midpoint is None:
            continue
        if midpoint[1] < transom_y - EPS:
            below_transom.append(member_id)
        else:
            above_transom.append(member_id)

    profiles["fixed_mullion"] = sorted(set(below_transom))

    member_x: dict[int, float] = {}
    for member_id in above_transom:
        midpoint = _member_midpoint(member_id, incidences, coordinates)
        if midpoint is None:
            continue
        member_x[member_id] = midpoint[0]

    if not member_x:
        profiles["central_meeting"] = []
        profiles["interlock"] = []
        return profiles

    columns: list[list[float]] = []
    for x in sorted(member_x.values()):
        if not columns or abs(x - columns[-1][-1]) > EPS * 10:
            columns.append([x])
        else:
            columns[-1].append(x)
    column_centers = [sum(group) / len(group) for group in columns]
    central_x = median(column_centers)

    def _nearest_center(x: float) -> float:
        return min(column_centers, key=lambda center: abs(center - x))

    central_ids: list[int] = []
    interlock_ids: list[int] = []
    for member_id, mid_x in member_x.items():
        nearest = _nearest_center(mid_x)
        if abs(nearest - central_x) <= EPS * 10:
            central_ids.append(member_id)
        else:
            interlock_ids.append(member_id)

    # Minority PRIS among above-transom members is typically interlock (e.g. IZ 421).
    # Geometry already places them off-center; hint is informational / future disambiguation.
    _ = _minority_pris_member_ids(member_pris, list(member_x.keys()))

    profiles["central_meeting"] = sorted(set(central_ids))
    profiles["interlock"] = sorted(set(interlock_ids))
    return profiles


def classify_casement_members(
    *,
    member_ids: list[int],
    member_incidences: dict[int, tuple[int, int]] | None,
    node_coordinates: dict[int, tuple[float, float, float]] | None,
    generation_request: dict[str, Any] | None = None,
    member_pris: dict[int, PrisFingerprint] | None = None,
) -> dict[str, list[int]]:
    """Return Casement profile → member_id lists (always includes all five keys)."""
    valid_ids = set(member_ids)
    request_map = _request_profile_map(generation_request)
    if request_map is not None:
        profiles = _validate_and_disjoint(request_map, valid_ids)
        LOGGER.info(
            "[STAAD][CASEMENT] Using request-supplied casement_profiles mapping: %s",
            {name: profiles[name] for name in CASEMENT_PROFILE_NAMES},
        )
        return profiles

    profiles = classify_casement_members_from_geometry(
        member_ids=member_ids,
        member_incidences=member_incidences,
        node_coordinates=node_coordinates,
        member_pris=member_pris,
    )
    profiles = _validate_and_disjoint(profiles, valid_ids)
    LOGGER.info(
        "[STAAD][CASEMENT] Resolved topology:\n"
        "Interlock: %s\n"
        "Central Meeting: %s\n"
        "Fixed Mullion: %s\n"
        "Horizontal: %s\n"
        "Outer: %s",
        profiles["interlock"],
        profiles["central_meeting"],
        profiles["fixed_mullion"],
        profiles["horizontal"],
        profiles["outer"],
    )
    return profiles
