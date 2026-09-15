import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from engine.errors import AnalysisError
from engine.parser import parse_std, parse_text
from engine.profiles import classify_unitized
from engine.solver import solve

ROOT = Path(__file__).parents[1]


def custom_unitized(
    widths, panels, *, stiffness=1.0, individual_pris=False, stack_gap=0.14
):
    """Independent test generator; no application topology is assumed in production."""
    xs = np.cumsum([0.0, *widths])
    levels = [0.0]
    tops = []
    supports = []
    for segments in panels:
        for segment in segments:
            levels.append(levels[-1] + segment)
        tops.append(levels[-1])
        supports.append(len(levels) - 1)
        levels.append(levels[-1] + stack_gap)

    def node(c, r):
        return 101 + c * len(levels) + r

    nodes = [
        f"{node(c, r)} {x} {y} 0"
        for c, x in enumerate(xs)
        for r, y in enumerate(levels)
    ]
    members = []
    roles = {k: [] for k in ["mullion", "stack", "head", "sill", "transom"]}
    releases = []
    stacks = [levels[i + 1] for i in supports[:-1]]
    for c in range(len(xs)):
        for r in range(len(levels) - 1):
            mid = 301 + len(members)
            members.append(f"{mid} {node(c, r)} {node(c, r + 1)}")
            roles["mullion"].append(mid)
            if levels[r] in stacks:
                releases.append(f"{mid} START FX MY MZ")
    loads = []
    for r, y in enumerate(levels):
        if r in supports:
            continue
        role = (
            "sill"
            if r == 0
            else "head"
            if r == len(levels) - 1
            else "stack"
            if y in stacks
            else "transom"
        )
        for c, w in enumerate(widths):
            mid = 301 + len(members)
            members.append(f"{mid} {node(c, r)} {node(c + 1, r)}")
            roles[role].append(mid)
            if role != "sill":
                releases += [f"{mid} START MY MZ", f"{mid} END MY MZ"]
            loads += [
                f"{mid} CON GY {-100 * w} {w / 4}",
                f"{mid} CON GY {-100 * w} {3 * w / 4}",
            ]
    pris = []
    for i, role in enumerate(reversed(roles)):
        mids = roles[role]
        if not mids:
            continue
        property_line = f"PRIS AX {(0.001 + i * 0.0001)} IX .0000002 IY .0000003 IZ {(i + 1) * 0.000001 * stiffness} AY .0008 AZ .0007"
        if individual_pris:
            pris.extend(f"{mid} {property_line}" for mid in mids)
        else:
            pris.append(" ".join(map(str, mids)) + " " + property_line)
    pins = " ".join(str(node(c, r)) for c in range(len(xs)) for r in supports)
    rollers = " ".join(str(node(c, 0)) for c in range(len(xs)))
    text = "\n".join(
        [
            "STAAD SPACE",
            "UNIT METER NEWTON",
            "JOINT COORDINATES",
            "; ".join(nodes),
            "MEMBER INCIDENCES",
            "; ".join(members),
            "DEFINE MATERIAL START",
            "ISOTROPIC ALUMINUM",
            "E 68947600000",
            "POISSON .33",
            "DENSITY 27101.8",
            "END DEFINE MATERIAL",
            "CONSTANTS",
            "MATERIAL ALUMINUM ALL",
            "BETA 90 ALL",
            "MEMBER PROPERTY AMERICAN",
            *pris,
            "SUPPORTS",
            f"{pins} PINNED",
            f"{rollers} FIXED BUT FY MX MY MZ",
            "MEMBER RELEASE",
            *releases,
            "LOAD 7 TITLE GRAVITY",
            "SELFWEIGHT Y -1",
            "MEMBER LOAD",
            *loads,
            "LOAD 13 TITLE WIND",
            "FLOOR LOAD",
            "ZRANGE -1 1 FLOAD -1200 GZ",
            "LOAD COMB 21 SERVICE",
            "7 1 13 1",
            "LOAD COMB 22 REVERSE",
            "7 1 13 -1",
            "PERFORM ANALYSIS",
            "FINISH",
        ]
    )
    return text, {k: v for k, v in roles.items() if v}


@pytest.mark.parametrize(
    "widths,panels,stack_gap",
    [
        ([1.2], [[1.4, 3.26]], 0.07),
        ([1.1, 1.7], [[1.2, 2.86], [1.1, 3.3, 0.86]], 0.12),
        ([1.1, 1.4, 1.6, 1.25, 1.8], [[1.0, 2.4], [1.2, 2.1], [1.3, 3.0, 0.8]], 0.25),
    ],
)
def test_variable_bays_panels_geometry_and_cases(widths, panels, stack_gap):
    text, roles = custom_unitized(
        widths, panels, individual_pris=True, stack_gap=stack_gap
    )
    model = parse_text(text)
    classified, _ = classify_unitized(model)
    assert classified == roles
    result = solve(model)
    assert set(result.cases) == {7, 13, 21, 22}
    assert result.diagnostics["relative_residual"] < 1e-9
    np.testing.assert_allclose(
        result.cases[21].displacement,
        result.cases[7].displacement + result.cases[13].displacement,
        atol=1e-12,
    )
    total_height = max(p[1] for p in model.nodes.values())
    wind_reaction = sum(
        result.cases[13].reactions[6 * result.node_indices[n] + 2]
        for n in model.supports
    )
    assert wind_reaction == pytest.approx(1200 * sum(widths) * total_height, rel=1e-9)
    for n, dofs in model.supports.items():
        if 1 not in dofs:
            assert abs(result.cases[7].reactions[6 * result.node_indices[n] + 1]) < 1e-6


@pytest.mark.parametrize("name", ["USG_1", "USG_2"])
def test_supplied_classification_matches_reference_and_ignores_pris_order(name):
    p = ROOT / "examples/usg" / f"{name}.std"
    text = p.read_text()
    reference = json.loads(p.with_name(name + "-results.json").read_text())["result"]
    expected = classify_unitized(parse_text(text))[0]
    assert {role: len(ids) for role, ids in expected.items()} == reference["profiles"][
        "member_counts"
    ]
    for role, ids in expected.items():
        governing = reference["properties"][role]["bending_moment"]["major"][
            "member_id"
        ]
        assert governing in ids
    lines = text.splitlines()
    idx = [i for i, line in enumerate(lines) if " PRIS " in line]
    reversed_lines = [lines[i] for i in idx][::-1]
    for i, line in zip(idx, reversed_lines):
        lines[i] = line
    assert classify_unitized(parse_text("\n".join(lines)))[0] == expected


def test_renumber_translation_and_reversed_members_do_not_change_roles():
    m = parse_std(ROOT / "examples/usg/USG_2.std")
    expected, _ = classify_unitized(m)
    translated = deepcopy(m)
    nmap = {n: 9999 - n for n in m.nodes}
    mmap = {n: 7000 - n for n in m.members}
    translated.nodes = {
        nmap[n]: p + np.array([1000.0, 5000.0, 300.0]) for n, p in m.nodes.items()
    }
    translated.members = {}
    for old, member in m.members.items():
        x = deepcopy(member)
        x.id = mmap[old]
        x.start = nmap[member.end]
        x.end = nmap[member.start]
        x.releases = {(i + 6) % 12 for i in member.releases}
        translated.members[x.id] = x
    actual, _ = classify_unitized(translated)
    assert actual == {
        role: sorted(mmap[i] for i in mids) for role, mids in expected.items()
    }


def test_custom_stiffness_has_physical_effect_and_no_fixed_profile_values():
    text, _ = custom_unitized([1.2, 1.5], [[1.4, 3.26]])
    model = parse_text(text)
    baseline = solve(model)
    twice = deepcopy(model)
    twice.materials["ALUMINUM"].e *= 2
    changed = solve(twice)
    np.testing.assert_allclose(
        changed.cases[13].displacement,
        baseline.cases[13].displacement / 2,
        rtol=1e-8,
        atol=1e-12,
    )
    for member in twice.members.values():
        member.section.iz *= 1.7
    final = solve(twice)
    assert np.linalg.norm(final.cases[13].displacement) < np.linalg.norm(
        changed.cases[13].displacement
    )


def test_explicit_mapping_is_complete_and_validated():
    model = parse_std(ROOT / "examples/usg/USG_1.std")
    groups, _ = classify_unitized(model)
    assert classify_unitized(model, {"profile_member_ids": groups})[0] == groups
    with pytest.raises(AnalysisError):
        classify_unitized(model, {"profile_member_ids": {"mullion": [1]}})
    with pytest.raises(AnalysisError):
        classify_unitized(model, {"profile_member_ids": {"mullion": [1, 1]}})


def test_changed_wind_load_scales_response_and_keeps_gravity():
    text, _ = custom_unitized([1.3, 1.7], [[1.2, 2.1]])
    original = solve(parse_text(text))
    changed = solve(parse_text(text.replace("FLOAD -1200", "FLOAD -2100")))
    np.testing.assert_allclose(
        changed.cases[13].displacement,
        1.75 * original.cases[13].displacement,
        rtol=1e-9,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        changed.cases[7].displacement,
        original.cases[7].displacement,
        rtol=1e-9,
        atol=1e-12,
    )


def test_equivalent_pris_units_preserve_canonical_properties_and_response():
    text = (ROOT / "examples/usg/USG_1.std").read_text()
    records = []
    for line in text.splitlines():
        if " PRIS " in line:
            a, b = line.split(" PRIS ")
            fields = b.split()
            for i in range(0, len(fields), 2):
                power = (
                    2
                    if fields[i] in {"AX", "AY", "AZ"}
                    else 4
                    if fields[i] in {"IX", "IY", "IZ"}
                    else 1
                )
                fields[i + 1] = format(float(fields[i + 1]) * 10**power, ".15g")
            line = a + " PRIS " + " ".join(fields)
        elif line == "UNIT CM KN":
            line = "UNIT MMS KN"
        records.append(line)
    original = parse_text(text)
    changed = parse_text("\n".join(records))
    from dataclasses import asdict

    for mid in original.members:
        assert asdict(original.members[mid].section) == pytest.approx(
            asdict(changed.members[mid].section)
        )
    a, b = solve(original), solve(changed)
    for cid in a.cases:
        np.testing.assert_allclose(
            a.cases[cid].displacement, b.cases[cid].displacement, rtol=1e-8, atol=1e-12
        )


def test_reordered_property_rows_preserve_entire_unitized_payload(tmp_path):
    from engine.adapter import build_payload, make_config

    p = ROOT / "examples/usg/USG_1.std"
    text = p.read_text()
    records = text.splitlines()
    idx = [i for i, line in enumerate(records) if " PRIS " in line]
    for i, line in zip(idx, [records[i] for i in idx][::-1]):
        records[i] = line
    changed = tmp_path / "reordered.std"
    changed.write_text("\n".join(records))
    a = build_payload(solve(parse_std(p)), make_config(p, "fully_unitized"))
    b = build_payload(solve(parse_std(changed)), make_config(changed, "fully_unitized"))
    for key in ("properties", "profiles", "physical_response"):
        assert a[key] == b[key]


@pytest.mark.parametrize(
    "role_map",
    [
        {"mullion": [True]},
        {"mullion": "1 TO 3"},
        {"unknown": []},
        {"mullion": [999999]},
        {"mullion": [1], "head": [1]},
    ],
)
def test_invalid_explicit_profile_mapping_is_rejected(role_map):
    with pytest.raises(AnalysisError, match="profile_member_ids|mapping"):
        classify_unitized(
            parse_std(ROOT / "examples/usg/USG_1.std"), {"profile_member_ids": role_map}
        )
