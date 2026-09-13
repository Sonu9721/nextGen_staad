"""Casement profile-wise signed BM/SF/AF/DF envelope extraction.

Isolated from Sliding/Fully Unitized absolute-max envelopes.
Reuses existing OpenSTAAD low-level getters and unit converters only.
"""

from __future__ import annotations

import logging
from typing import Any

from casement_classifier import CASEMENT_PROFILE_NAMES, classify_casement_members

LOGGER = logging.getLogger("staad_max_extractor")


def calculate_signed_min_max(
    samples: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (max_sample, min_sample) by signed value. Empty → (None, None)."""
    if not samples:
        return None, None
    max_sample = max(samples, key=lambda item: float(item["value"]))
    min_sample = min(samples, key=lambda item: float(item["value"]))
    return max_sample, min_sample


def build_governing_result(sample: dict[str, Any] | None) -> dict[str, Any] | None:
    if sample is None:
        return None
    result: dict[str, Any] = {
        "value": round(float(sample["value"]), 6),
    }
    for key in (
        "member_id",
        "node_id",
        "station",
        "location",
        "load_case",
        "load_case_name",
        "axis",
        "direction",
    ):
        if key in sample and sample[key] is not None:
            value = sample[key]
            if key in {"station"} and isinstance(value, float):
                result[key] = round(value, 6)
            else:
                result[key] = value
    return result


def _metric_block(
    unit: str,
    max_sample: dict[str, Any] | None,
    min_sample: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "unit": unit,
        "max": build_governing_result(max_sample),
        "min": build_governing_result(min_sample),
    }


def _collect_bm_samples(
    *,
    extractor: Any,
    session: Any,
    member_ids: list[int],
    load_cases: list[int],
    moment_axis: str,
    source_units: Any,
    load_case_names: dict[int, str],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for member_id in member_ids:
        for load_case in load_cases:
            extremes = extractor.get_member_bending_moment_extremes(
                session.output,
                member_id,
                load_case,
                moment_axis,
            )
            if not extremes:
                continue
            for kind, value_key, station_key in (
                ("member-minimum", "minimum_value", "minimum_station"),
                ("member-maximum", "maximum_value", "maximum_station"),
            ):
                raw_value = extremes.get(value_key)
                if raw_value is None:
                    continue
                signed = extractor.convert_moment_to_kn_m(
                    float(raw_value),
                    source_units.force_unit,
                    source_units.length_unit,
                )
                sample: dict[str, Any] = {
                    "value": signed,
                    "member_id": int(member_id),
                    "station": float(extremes.get(station_key)) if extremes.get(station_key) is not None else None,
                    "location": kind,
                    "load_case": int(load_case),
                    "axis": moment_axis.upper(),
                }
                name = load_case_names.get(int(load_case))
                if name:
                    sample["load_case_name"] = name
                samples.append(sample)
    return samples


def _collect_force_samples(
    *,
    extractor: Any,
    session: Any,
    member_ids: list[int],
    load_cases: list[int],
    force_axis: str,
    source_units: Any,
    load_case_names: dict[int, str],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    force_index = extractor.FORCE_INDEX[force_axis.upper()]
    for member_id in member_ids:
        for load_case in load_cases:
            for end in (0, 1):
                try:
                    forces = extractor.get_member_end_forces(
                        session.output,
                        member_id,
                        end,
                        load_case,
                    )
                except Exception as exc:
                    LOGGER.debug(
                        "[STAAD][CASEMENT] End force query failed member=%s lc=%s end=%s: %s",
                        member_id,
                        load_case,
                        end,
                        exc,
                    )
                    continue
                raw_value = forces[force_index]
                signed = extractor.convert_force_to_kn(float(raw_value), source_units.force_unit)
                sample: dict[str, Any] = {
                    "value": signed,
                    "member_id": int(member_id),
                    "station": float(end),
                    "location": "start" if end == 0 else "end",
                    "load_case": int(load_case),
                    "axis": force_axis.upper(),
                }
                name = load_case_names.get(int(load_case))
                if name:
                    sample["load_case_name"] = name
                samples.append(sample)
    return samples


def _collect_df_samples(
    *,
    extractor: Any,
    session: Any,
    member_ids: list[int],
    node_ids: list[int],
    load_cases: list[int],
    displacement_axis: str,
    source_units: Any,
    load_case_names: dict[int, str],
) -> list[dict[str, Any]]:
    """Collect RESULTANT displacement samples for profile nodes/members.

    Member stations prefer intermediate *deflection* (relative to the member chord)
    so vertical profiles are not dominated by shared absolute motion at horizontal
    junctions. Exclusive nodal RESULTANT samples are still included.
    """
    samples: list[dict[str, Any]] = []
    axis = displacement_axis.upper() if displacement_axis.upper() != "R" else "RESULTANT"

    for load_case in load_cases:
        for node_id in node_ids:
            try:
                displacements = extractor.get_node_displacements(session.output, node_id, load_case)
                raw_value = extractor._node_displacement_component(displacements, axis)
            except Exception as exc:
                LOGGER.debug(
                    "[STAAD][CASEMENT] Node displacement failed node=%s lc=%s: %s",
                    node_id,
                    load_case,
                    exc,
                )
                continue
            converted = extractor.convert_length(raw_value, source_units.length_unit, "MM")
            sample: dict[str, Any] = {
                "value": converted,
                "node_id": int(node_id),
                "load_case": int(load_case),
                "direction": axis,
            }
            name = load_case_names.get(int(load_case))
            if name:
                sample["load_case_name"] = name
            samples.append(sample)

        for member_id in member_ids:
            length = extractor._get_member_length(session.geometry, member_id)
            if length is None or length <= 0:
                continue
            member_max_value = -1.0
            member_max_station = None
            for station in extractor._sample_stations(length, extractor.MEMBER_DEFLECTION_SAMPLE_POINTS):
                try:
                    deflection = extractor.get_member_intermediate_deflection(
                        session.output,
                        member_id,
                        station,
                        load_case,
                    )
                    raw_value = extractor._member_deflection_component(deflection, axis)
                except Exception as exc:
                    LOGGER.debug(
                        "[STAAD][CASEMENT] Member deflection failed member=%s lc=%s st=%s: %s",
                        member_id,
                        load_case,
                        station,
                        exc,
                    )
                    continue
                if raw_value is None:
                    continue
                converted = extractor.convert_length(raw_value, source_units.length_unit, "MM")
                if converted > member_max_value:
                    member_max_value = converted
                    member_max_station = station
            if member_max_value < 0:
                continue
            sample = {
                "value": float(member_max_value),
                "member_id": int(member_id),
                "station": float(member_max_station) if member_max_station is not None else None,
                "load_case": int(load_case),
                "direction": axis,
            }
            name = load_case_names.get(int(load_case))
            if name:
                sample["load_case_name"] = name
            samples.append(sample)
    return samples


def _node_ids_for_members(topology: Any, member_ids: list[int]) -> list[int]:
    incidences = getattr(topology, "member_incidences", None) or {}
    node_ids: set[int] = set()
    for member_id in member_ids:
        incidence = incidences.get(member_id)
        if incidence is None:
            continue
        node_ids.add(incidence[0])
        node_ids.add(incidence[1])
    return sorted(node_ids)


def extract_casement_profile_envelopes(
    *,
    extractor: Any,
    session: Any,
    config: Any,
    topology: Any,
    member_ids: list[int],
    selected_load_cases: list[int],
    runtime_units: Any,
    load_case_names: dict[int, str],
    generation_request: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the top-level `casement` JSON object with signed profile envelopes."""
    member_pris = None
    try:
        parsed = extractor.parse_member_property_lines(config.file_path)
        member_pris = parsed.member_pris
    except Exception as exc:
        LOGGER.warning("[STAAD][CASEMENT] Unable to parse PRIS fingerprints: %s", exc)

    profile_member_ids = classify_casement_members(
        member_ids=member_ids,
        member_incidences=topology.member_incidences,
        node_coordinates=topology.node_coordinates,
        generation_request=generation_request,
        member_pris=member_pris,
    )

    # Cache member-level force/moment samples once across all profiles.
    all_classified = sorted(
        {
            member_id
            for ids in profile_member_ids.values()
            for member_id in ids
        }
    )
    moment_axis = (getattr(config, "moment_axis", None) or "MZ").upper()
    displacement_axis = getattr(config, "displacement_axis", "RESULTANT") or "RESULTANT"

    bm_cache = _collect_bm_samples(
        extractor=extractor,
        session=session,
        member_ids=all_classified,
        load_cases=selected_load_cases,
        moment_axis=moment_axis,
        source_units=runtime_units,
        load_case_names=load_case_names,
    )
    sf_cache = _collect_force_samples(
        extractor=extractor,
        session=session,
        member_ids=all_classified,
        load_cases=selected_load_cases,
        force_axis="FY",
        source_units=runtime_units,
        load_case_names=load_case_names,
    )
    af_cache = _collect_force_samples(
        extractor=extractor,
        session=session,
        member_ids=all_classified,
        load_cases=selected_load_cases,
        force_axis="FX",
        source_units=runtime_units,
        load_case_names=load_case_names,
    )

    # Displacement cache per profile (nodes differ); still query each member once globally.
    all_nodes = _node_ids_for_members(topology, all_classified)
    df_cache = _collect_df_samples(
        extractor=extractor,
        session=session,
        member_ids=all_classified,
        node_ids=all_nodes,
        load_cases=selected_load_cases,
        displacement_axis=displacement_axis,
        source_units=runtime_units,
        load_case_names=load_case_names,
    )

    moment_unit = getattr(config, "moment_output_unit", "kN-m") or "kN-m"
    displacement_unit = getattr(config, "displacement_output_unit", "mm") or "mm"

    profiles_payload: dict[str, Any] = {}
    for profile_name in CASEMENT_PROFILE_NAMES:
        group_ids = profile_member_ids.get(profile_name, [])
        group_id_set = set(group_ids)
        group_nodes = set(_node_ids_for_members(topology, group_ids))

        bm_samples = [s for s in bm_cache if s.get("member_id") in group_id_set]
        sf_samples = [s for s in sf_cache if s.get("member_id") in group_id_set]
        af_samples = [s for s in af_cache if s.get("member_id") in group_id_set]

        # Profile DF:
        # - relative member-chord deflection for profile members (matches PDF vertical DF)
        # - absolute nodal RESULTANT for nodes exclusive to this profile
        # - horizontal also includes absolute nodal RESULTANT at shared junctions
        #   (PDF horizontal DF ≈ global absolute midspan displacement)
        exclusive_nodes = group_nodes - {
            node_id
            for other_name, other_ids in profile_member_ids.items()
            if other_name != profile_name
            for node_id in _node_ids_for_members(topology, other_ids)
        }
        allowed_nodes = set(exclusive_nodes)
        if profile_name == "horizontal":
            allowed_nodes = set(group_nodes)

        df_samples = [
            s
            for s in df_cache
            if s.get("member_id") in group_id_set
            or (
                s.get("node_id") is not None
                and s.get("member_id") is None
                and s.get("node_id") in allowed_nodes
            )
        ]

        bm_max, bm_min = calculate_signed_min_max(bm_samples)
        sf_max, sf_min = calculate_signed_min_max(sf_samples)
        af_max, af_min = calculate_signed_min_max(af_samples)
        df_max, df_min = calculate_signed_min_max(df_samples)

        LOGGER.info(
            "[STAAD][CASEMENT] %s BM max=%s min=%s | SF max=%s min=%s | AF max=%s min=%s | DF max=%s min=%s",
            profile_name,
            None if bm_max is None else bm_max["value"],
            None if bm_min is None else bm_min["value"],
            None if sf_max is None else sf_max["value"],
            None if sf_min is None else sf_min["value"],
            None if af_max is None else af_max["value"],
            None if af_min is None else af_min["value"],
            None if df_max is None else df_max["value"],
            None if df_min is None else df_min["value"],
        )

        profiles_payload[profile_name] = {
            "member_ids": list(group_ids),
            "bm": _metric_block(moment_unit, bm_max, bm_min),
            "sf": _metric_block("kN", sf_max, sf_min),
            "af": _metric_block("kN", af_max, af_min),
            "df": _metric_block(displacement_unit, df_max, df_min),
        }

    return {
        "extraction_flow": "casement",
        "profiles": profiles_payload,
    }


def build_empty_casement_payload() -> dict[str, Any]:
    """Safe null casement object when classification/extraction fails soft."""
    profiles: dict[str, Any] = {}
    for name in CASEMENT_PROFILE_NAMES:
        profiles[name] = {
            "member_ids": [],
            "bm": _metric_block("kN-m", None, None),
            "sf": _metric_block("kN", None, None),
            "af": _metric_block("kN", None, None),
            "df": _metric_block("mm", None, None),
        }
    return {"extraction_flow": "casement", "profiles": profiles}
