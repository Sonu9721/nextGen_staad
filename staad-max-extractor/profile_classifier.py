"""Profile group classification for Fully Unitized STAAD models."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

EPS = 1e-6
_NUMBER_PATTERN = r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?"
_PRIS_LINE_RE = re.compile(
    rf"^(?P<ranges>.+?)\s+PRIS\s+AX\s+(?P<ax>{_NUMBER_PATTERN})\s+"
    rf"IX\s+(?P<ix>{_NUMBER_PATTERN})\s+"
    rf"IY\s+(?P<iy>{_NUMBER_PATTERN})\s+"
    rf"IZ\s+(?P<iz>{_NUMBER_PATTERN})\s+"
    rf"YD\s+(?P<yd>{_NUMBER_PATTERN})\s+"
    rf"ZD\s+(?P<zd>{_NUMBER_PATTERN})\s*;?\s*$",
    re.IGNORECASE,
)

PROPERTY_GROUP_ORDER: tuple[str, ...] = ("mullion", "stack", "head", "sill", "transom")


def groups_for_property_line_count(count: int) -> list[PropertyGroup]:
    if count <= 0:
        return []
    if count >= len(PROPERTY_GROUP_ORDER):
        return [PropertyGroup(name) for name in PROPERTY_GROUP_ORDER]
    available = [
        PropertyGroup(name)
        for name in PROPERTY_GROUP_ORDER
        if name != "stack" or count >= 5
    ]
    return available[:count]


class PropertyGroup(str, Enum):
    MULLION = "mullion"
    STACK = "stack"
    HEAD = "head"
    SILL = "sill"
    TRANSOM = "transom"


@dataclass(frozen=True)
class PrisFingerprint:
    ax: float
    ix: float
    iy: float
    iz: float
    yd: float
    zd: float

    @classmethod
    def from_match(cls, match: re.Match[str]) -> PrisFingerprint:
        return cls(
            ax=float(match.group("ax")),
            ix=float(match.group("ix")),
            iy=float(match.group("iy")),
            iz=float(match.group("iz")),
            yd=float(match.group("yd")),
            zd=float(match.group("zd")),
        )

    def matches(self, other: PrisFingerprint) -> bool:
        return (
            _is_equal(self.ax, other.ax)
            and _is_equal(self.ix, other.ix)
            and _is_equal(self.iy, other.iy)
            and _is_equal(self.iz, other.iz)
            and _is_equal(self.yd, other.yd)
            and _is_equal(self.zd, other.zd)
        )


@dataclass(frozen=True)
class PanelGeometry:
    panel_top_y: float
    panel_base_y: float
    is_final_panel: bool


@dataclass(frozen=True)
class PanelGeometryContext:
    assembly_top_y: float
    panels: tuple[PanelGeometry, ...]


@dataclass(frozen=True)
class ParsedMemberProperties:
    member_groups: dict[int, PropertyGroup]
    member_pris: dict[int, PrisFingerprint]
    group_fingerprints: dict[PropertyGroup, PrisFingerprint]


def _is_equal(a: float, b: float) -> bool:
    return abs(a - b) <= EPS


def normalize_extraction_flow(value: str | None) -> str:
    if not value:
        return "standard"
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"fully_unitized", "unitized", "fullyunitized"}:
        return "fully_unitized"
    if normalized == "casement":
        return "casement"
    return "standard"


def resolve_extraction_flow(form_value: str | None, generation_request: dict[str, Any] | None) -> str:
    flow = normalize_extraction_flow(form_value)
    if flow in {"fully_unitized", "casement"}:
        return flow
    if generation_request is None:
        return "standard"

    for key in ("flowType", "flow_type", "extractionFlow", "extraction_flow"):
        raw = generation_request.get(key)
        if not isinstance(raw, str):
            continue
        normalized = normalize_extraction_flow(raw)
        if normalized in {"fully_unitized", "casement"}:
            return normalized
    return "standard"


def load_generation_request(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def expand_staad_member_ranges(tokens: list[str]) -> list[int]:
    member_ids: list[int] = []
    index = 0
    while index < len(tokens):
        token = tokens[index].upper()
        if token == "TO":
            raise ValueError("STAAD member range missing start before TO")
        start = int(tokens[index])
        index += 1
        if index < len(tokens) and tokens[index].upper() == "TO":
            if index + 1 >= len(tokens):
                raise ValueError("STAAD member range missing end after TO")
            end = int(tokens[index + 1])
            index += 2
            if end < start:
                start, end = end, start
            member_ids.extend(range(start, end + 1))
        else:
            member_ids.append(start)
    return member_ids


def _tokenize_member_ranges(range_text: str) -> list[str]:
    cleaned = range_text.strip().rstrip(";")
    return [token for token in re.split(r"\s+", cleaned) if token]


def parse_member_property_lines(file_path: Path) -> ParsedMemberProperties:
    member_groups: dict[int, PropertyGroup] = {}
    member_pris: dict[int, PrisFingerprint] = {}
    group_fingerprints: dict[PropertyGroup, PrisFingerprint] = {}
    in_property_section = False
    pris_lines: list[tuple[PrisFingerprint, list[int]]] = []

    with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("*"):
                continue
            upper = line.upper()
            if upper == "MEMBER PROPERTY AMERICAN" or upper == "MEMBER PROPERTY":
                in_property_section = True
                continue
            if not in_property_section:
                continue
            if upper.startswith("MEMBER RELEASE") or upper.startswith("MEMBER LOAD") or upper.startswith("CONSTANTS"):
                break
            if upper.startswith("UNIT ") or upper.startswith("LOAD ") or upper.startswith("PERFORM ANALYSIS"):
                break

            match = _PRIS_LINE_RE.match(line)
            if match is None:
                continue
            fingerprint = PrisFingerprint.from_match(match)
            member_range_tokens = _tokenize_member_ranges(match.group("ranges"))
            member_ids = expand_staad_member_ranges(member_range_tokens)
            pris_lines.append((fingerprint, member_ids))

    assigned_groups = groups_for_property_line_count(len(pris_lines))
    for group, (fingerprint, member_ids) in zip(assigned_groups, pris_lines, strict=False):
        group_fingerprints[group] = fingerprint
        for member_id in member_ids:
            member_groups[member_id] = group
            member_pris[member_id] = fingerprint

    return ParsedMemberProperties(
        member_groups=member_groups,
        member_pris=member_pris,
        group_fingerprints=group_fingerprints,
    )


def build_panel_geometry(generation_request: dict[str, Any] | None) -> PanelGeometryContext | None:
    if generation_request is None:
        return None
    panels = generation_request.get("panels")
    if not isinstance(panels, list) or not panels:
        return None

    panel_geometries: list[PanelGeometry] = []
    panel_base_y = 0.0
    for index, panel in enumerate(panels):
        if not isinstance(panel, dict):
            continue
        segments = panel.get("segments")
        if not isinstance(segments, list) or not segments:
            continue
        panel_top_y = panel_base_y + sum(float(segment) for segment in segments)
        panel_geometries.append(
            PanelGeometry(
                panel_top_y=panel_top_y,
                panel_base_y=panel_base_y,
                is_final_panel=index == len(panels) - 1,
            )
        )
        panel_base_y = panel_top_y

    if not panel_geometries:
        return None

    return PanelGeometryContext(
        assembly_top_y=panel_geometries[-1].panel_top_y,
        panels=tuple(panel_geometries),
    )


def classify_horizontal(y: float, panel_geometry: PanelGeometryContext | None, assembly_top_y: float) -> PropertyGroup:
    if _is_equal(y, 0.0):
        return PropertyGroup.SILL
    if _is_equal(y, assembly_top_y):
        return PropertyGroup.HEAD
    if panel_geometry is not None:
        for panel in panel_geometry.panels:
            if not panel.is_final_panel and _is_equal(y, panel.panel_top_y):
                return PropertyGroup.STACK
    return PropertyGroup.TRANSOM


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


def _infer_panel_geometry_from_stack_members(
    member_groups: dict[int, PropertyGroup],
    member_incidences: dict[int, tuple[int, int]],
    node_coordinates: dict[int, tuple[float, float, float]],
    assembly_top_y: float,
) -> PanelGeometryContext | None:
    stack_ys: list[float] = []
    for member_id, group in member_groups.items():
        if group != PropertyGroup.STACK:
            continue
        y_value = _horizontal_member_y(member_id, member_incidences, node_coordinates)
        if y_value is not None:
            stack_ys.append(y_value)

    unique_ys = sorted({y for y in stack_ys})
    if not unique_ys:
        return None

    panels: list[PanelGeometry] = []
    panel_base_y = 0.0
    for index, panel_top_y in enumerate(unique_ys):
        panels.append(
            PanelGeometry(
                panel_top_y=panel_top_y,
                panel_base_y=panel_base_y,
                is_final_panel=False,
            )
        )
        panel_base_y = panel_top_y
    panels.append(
        PanelGeometry(
            panel_top_y=assembly_top_y,
            panel_base_y=panel_base_y,
            is_final_panel=True,
        )
    )
    return PanelGeometryContext(assembly_top_y=assembly_top_y, panels=tuple(panels))


def classify_profile_members(
    *,
    parsed_properties: ParsedMemberProperties,
    member_ids: list[int],
    member_incidences: dict[int, tuple[int, int]] | None,
    node_coordinates: dict[int, tuple[float, float, float]] | None,
    generation_request: dict[str, Any] | None = None,
) -> dict[str, list[int]]:
    incidences = member_incidences or {}
    coordinates = node_coordinates or {}
    assembly_top_y = max((coords[1] for coords in coordinates.values()), default=0.0)
    panel_geometry = build_panel_geometry(generation_request)
    if panel_geometry is None:
        panel_geometry = _infer_panel_geometry_from_stack_members(
            parsed_properties.member_groups,
            incidences,
            coordinates,
            assembly_top_y,
        )

    fingerprint_to_groups: dict[PrisFingerprint, set[PropertyGroup]] = {}
    for group, fingerprint in parsed_properties.group_fingerprints.items():
        matched = None
        for existing in fingerprint_to_groups:
            if existing.matches(fingerprint):
                matched = existing
                break
        key = matched if matched is not None else fingerprint
        fingerprint_to_groups.setdefault(key, set()).add(group)

    ambiguous_fingerprints = {
        fingerprint for fingerprint, groups in fingerprint_to_groups.items() if len(groups) > 1
    }

    classified: dict[str, list[int]] = {group.value: [] for group in PropertyGroup}
    for member_id in member_ids:
        pris_group = parsed_properties.member_groups.get(member_id)
        if _is_vertical_member(member_id, incidences, coordinates):
            classified[PropertyGroup.MULLION.value].append(member_id)
            continue

        if pris_group is None:
            y_value = _horizontal_member_y(member_id, incidences, coordinates)
            if y_value is None:
                continue
            geometry_group = classify_horizontal(y_value, panel_geometry, assembly_top_y)
            classified[geometry_group.value].append(member_id)
            continue

        fingerprint = parsed_properties.member_pris.get(member_id)
        if fingerprint is not None and fingerprint in ambiguous_fingerprints:
            y_value = _horizontal_member_y(member_id, incidences, coordinates)
            if y_value is not None:
                geometry_group = classify_horizontal(y_value, panel_geometry, assembly_top_y)
                classified[geometry_group.value].append(member_id)
                continue

        classified[pris_group.value].append(member_id)

    return {
        group_name: sorted(set(ids))
        for group_name, ids in classified.items()
        if ids
    }
