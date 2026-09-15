"""Reproduce before/after, strict differences and full member diagnostic evidence."""

import hashlib
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import __version__
from engine.adapter import build_payload, make_config
from engine.comparison import Tolerances, compare
from engine.parser import parse_std
from engine.solver import solve


def main():
    target = ROOT / "validation/usg"
    target.mkdir(exist_ok=True)
    report = {}
    lines = [
        "# Fully unitized: measured before and after",
        "",
        "Both reference envelopes were evaluated against the unchanged STD files. Original baselines are retained in validation/usg-baseline. The standard acceptance thresholds are unchanged. A stricter investigation threshold (max 0.01%, 0.00002 kN/kN m or 0.001 mm) additionally exposes small differences; it is not a substitute for design qualification.",
        "",
        "The fixed defect is classification by PRIS row order. Canonical geometry and released splice levels now assign roles, and a complete explicit profile_member_ids mapping can override that convention. This preserves supplied-model results and supports reordered/custom property assignments.",
        "",
        "Moment values at reference stations agree within a few millionths of kN m; continuous peak stations differ. Displacement discrepancies persist at the reference point. Alternative shear coefficients and moment-area reconstructions were investigated but did not establish reference equivalence. No empirical adjustment was made.",
        "",
        "| Model / metric | Reference | Before | After | Absolute error | Relative error | Strict status |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    locations = [
        "",
        "## Governing members, stations and cases",
        "",
        "All station values below are normalized to metres. N/A is retained when the source does not supply a station; endpoint labels are not converted into lengths. Member/case differences at tied envelopes are distinct from value differences.",
        "",
        "| Model / metric | Ref member / node | Actual member / node | Ref station m | Actual station m | Ref / actual case | Location status |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for path in sorted((ROOT / "examples/usg").glob("*.std")):
        reference = json.loads(path.with_name(path.stem + "-results.json").read_text())
        baseline = json.loads(
            (
                ROOT / "validation/usg-baseline" / (path.stem + "-actual.json")
            ).read_text()
        )
        started = time.perf_counter()
        model = parse_std(path)
        parsed_at = time.perf_counter()
        stages = []

        def progress(percent, message, stages=stages, parsed_at=parsed_at):
            stages.append(
                {
                    "percent": percent,
                    "message": message,
                    "elapsed_seconds": time.perf_counter() - parsed_at,
                }
            )

        result = solve(model, progress=progress)
        solved_at = time.perf_counter()
        actual = build_payload(result, make_config(path, "fully_unitized"))
        enveloped_at = time.perf_counter()
        strict = compare(
            reference,
            actual,
            result,
            Tolerances(relative=1e-4, force=2e-5, moment=2e-5, displacement=0.001),
        )
        standard = compare(reference, actual, result)
        before = {
            r["metric"]: r["candidate"] for r in compare(reference, baseline)["metrics"]
        }
        for row in strict["metrics"]:
            row["before"] = before[row["metric"]]
            relative = (
                "N/A"
                if row["relative_difference"] is None
                else f"{100 * row['relative_difference']:.6f}%"
            )
            lines.append(
                f"| {path.stem} / {row['metric'].replace('properties.', '')} | {row['reference']} | {row['before']} | {row['candidate']} | {row['absolute_difference']:.9f} | {relative} | {row['value_status']} |"
            )
            expected_point, actual_point = (
                row["reference_location"],
                row["candidate_location"],
            )

            def identity(point):
                return (
                    f"member {point['member_id']}"
                    if point.get("member_id") is not None
                    else f"node {point['node_id']}"
                    if point.get("node_id") is not None
                    else "N/A"
                )

            def station(value):
                return "N/A" if value is None else f"{value:.9f}"

            locations.append(
                f"| {path.stem} / {row['metric'].replace('properties.', '')} | {identity(expected_point)} | {identity(actual_point)} | {station(row.get('reference_station_m'))} | {station(row.get('candidate_station_m'))} | {expected_point.get('load_case', expected_point.get('governing_load_case', 'N/A'))} / {actual_point.get('load_case', actual_point.get('governing_load_case', 'N/A'))} | {row['location_status']} |"
            )
        # Full selected-member/case details make the next independent comparison reviewable.
        selected = {
            r["reference_location"].get("member_id") for r in strict["metrics"]
        } - {None}
        debug = {}
        for cid, case in result.cases.items():
            debug[str(cid)] = {}
            for mid in sorted(selected):
                m = case.members[mid]
                e = m.element
                debug[str(cid)][str(mid)] = {
                    "start_node": e.member.start,
                    "end_node": e.member.end,
                    "start_m": model.nodes[e.member.start].tolist(),
                    "end_m": model.nodes[e.member.end].tolist(),
                    "length_m": e.length,
                    "local_axes": e.rotation.tolist(),
                    "section_SI": asdict(e.section),
                    "material_SI": asdict(e.material),
                    "release_dofs": e.released,
                    "local_end_displacements_m_rad": m.displacement.tolist(),
                    "local_end_forces_N_Nm": m.end_forces.tolist(),
                    "loads": [
                        {
                            "a_m": l.a,
                            "b_m": l.b,
                            "qa": l.qa.tolist(),
                            "qb": l.qb.tolist(),
                            "point": l.point,
                            "moment": l.moment,
                        }
                        for l in m.loads
                    ],
                    "stations": [
                        {
                            "x_m": x,
                            "force_N_Nm": m.internal(x).tolist(),
                            "translation_m": m.translation(x).tolist(),
                            "legacy_translation_m": m.translation(
                                x, legacy_section=True
                            ).tolist(),
                        }
                        for x in [e.length * i / 12 for i in range(13)]
                    ],
                }
        report[path.stem] = {
            "solver_version": __version__,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "nodes": len(model.nodes),
            "members": len(model.members),
            "diagnostics": result.diagnostics,
            "standard": standard,
            "strict": strict,
            "classification": actual["analysis"]["profile_classification"],
            "timings_seconds": {
                "parse_and_validate": parsed_at - started,
                "assembly_solve_and_recovery": solved_at - parsed_at,
                "profile_and_physical_envelopes": enveloped_at - solved_at,
                "total_analysis": enveloped_at - started,
                "solver_progress_boundaries": stages,
            },
        }
        (target / (path.stem + "-actual.json")).write_text(
            json.dumps(actual, indent=2, allow_nan=False)
        )
        (target / (path.stem + "-member-diagnostics.json")).write_text(
            json.dumps(debug, indent=2, allow_nan=False)
        )
        nodal = {
            str(cid): {
                str(n): {
                    "coordinates_m": model.nodes[n].tolist(),
                    "restraint_dofs": sorted(model.supports.get(n, set())),
                    "translation_rotation_m_rad": case.displacement[
                        6 * i : 6 * i + 6
                    ].tolist(),
                    "reaction_N_Nm": case.reactions[6 * i : 6 * i + 6].tolist(),
                }
                for n, i in result.node_indices.items()
            }
            for cid, case in result.cases.items()
        }
        (target / (path.stem + "-node-diagnostics.json")).write_text(
            json.dumps(nodal, indent=2, allow_nan=False), encoding="utf-8"
        )
        print(path.stem, standard["counts"], "strict", strict["counts"], flush=True)
    (target / "comparison.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(), "models": report},
            indent=2,
            allow_nan=False,
        )
    )
    lines += locations + [
        "",
        "The 13 diagnostic stations are for inspection only; physical extrema are solved continuously. Metadata comparisons, station conversions to metres and evaluation at the reference point are retained in comparison.json. Governing IDs can differ at tied members/cases; value comparison and metadata equality are reported separately.",
        "",
    ]
    (ROOT / "docs/USG_VALIDATION.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
