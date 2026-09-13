"""Feed numerical results into the original production envelope functions."""

from functools import lru_cache
import importlib.util
import sys
from pathlib import Path
from . import __version__
from .errors import AnalysisError, check_cancel
from .parser import parse_std
from .solver import solve
from .physical import build_physical_response


@lru_cache(maxsize=1)
def extractor_module():
    path = Path(__file__).resolve().parents[1] / "staad-max-extractor" / "extractor.py"
    spec = importlib.util.spec_from_file_location("inhouse_legacy_envelopes", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    root = str(path.parents[1])
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    return module


class NumericalOutput:
    def __init__(self, result, abort_check=None):
        self.result = result
        self.abort_check = abort_check

    def member(self, mid, case):
        check_cancel(self.abort_check)
        return self.result.cases[case].members[mid]

    def GetMemberEndForces(self, mid, end, case, local_flag=0):
        return self.member(mid, case).end_forces[6 * end : 6 * end + 6].tolist()

    def GetIntermediateMemberForcesAtDistance(self, mid, distance, case):
        return self.member(mid, case).internal(distance).tolist()

    @lru_cache(maxsize=20000)
    def GetMinMaxBendingMoment(self, mid, axis, case):
        return self.member(mid, case).extrema(axis)

    @lru_cache(maxsize=60000)
    def GetIntermediateMemberAbsTransDisplacements(self, mid, distance, case):
        return (
            self.member(mid, case).translation(distance, legacy_section=True).tolist()
        )

    @lru_cache(maxsize=60000)
    def GetIntermediateDeflectionAtDistance(self, mid, distance, case):
        return (
            self.member(mid, case)
            .translation(distance, relative=True, legacy_section=True)
            .tolist()
        )

    def GetNodeDisplacements(self, node, case):
        check_cancel(self.abort_check)
        i = 6 * self.result.node_indices[node]
        return self.result.cases[case].displacement[i : i + 6].tolist()

    def close(self):
        # Method caches contain self; clear after each job so results cannot leak.
        self.GetMinMaxBendingMoment.cache_clear()
        self.GetIntermediateMemberAbsTransDisplacements.cache_clear()
        self.GetIntermediateDeflectionAtDistance.cache_clear()


class NumericalGeometry:
    def __init__(self, result):
        self.result = result

    def GetMemberIncidence(self, mid):
        m = self.result.model.members[mid]
        return [m.start, m.end]

    def GetNodeCoordinates(self, node):
        return self.result.model.nodes[node].tolist()

    def GetBeamLength(self, mid):
        return self.result.elements[mid].length

    def GetMemberList(self):
        return sorted(self.result.elements)

    def GetMemberCount(self):
        return len(self.result.elements)

    def GetNodeList(self):
        return sorted(self.result.model.nodes)

    def GetNodeCount(self):
        return len(self.result.model.nodes)


def make_config(
    path,
    flow="standard",
    include_combinations=True,
    generation_request_path=None,
    progress=None,
):
    ex = extractor_module()
    return ex.ExtractionConfig(
        file_path=Path(path),
        load_case="all",
        moment_axis="MZ",
        moment_mode="internal-envelope",
        include_combinations=include_combinations,
        displacement_axis="RESULTANT",
        source_force_unit=None,
        source_length_unit=None,
        moment_output_unit="kN-m",
        displacement_output_unit="mm",
        analysis_timeout_seconds=180,
        poll_interval_seconds=0.1,
        launch_wait_seconds=0,
        startup_timeout_seconds=0,
        attach_timeout_seconds=0,
        output_path=None,
        extraction_flow=flow,
        generation_request_path=Path(generation_request_path)
        if generation_request_path
        else None,
        progress_callback=progress,
    )


def build_payload(result, config, abort_check=None):
    ex = extractor_module()
    model = result.model
    output = NumericalOutput(result, abort_check)
    topology = ex.ParsedModelTopology(
        sorted(model.members),
        sorted(model.nodes),
        {n: tuple(p) for n, p in model.nodes.items()},
        {m.id: (m.start, m.end) for m in model.members.values()},
    )
    session = ex.StaadSession(None, NumericalGeometry(result), output)
    units = ex.DetectedUnits("N", "METER")
    file_units = ex.DetectedUnits(model.file_units.force, model.file_units.length)
    selected, mode = ex.resolve_load_case_selection(
        config.load_case,
        sorted(result.cases),
        sorted(model.cases),
        config.include_combinations,
    )
    request = ex.load_generation_request(config.generation_request_path)
    flow = ex.resolve_extraction_flow(config.extraction_flow, request)
    shared = dict(
        session=session,
        config=config,
        topology=topology,
        load_case_mode=mode,
        selected_load_cases=selected,
        runtime_units=units,
        load_case_names=model.titles,
    )
    try:
        check_cancel(abort_check)
        global_envelope = ex._extract_property_envelope(
            **shared,
            member_ids=topology.member_ids,
            node_ids=topology.node_ids,
            include_minor_axes=True,
        )
        profiles = None
        envelopes = None
        casement = None
        if flow == "fully_unitized":
            profiles = ex.classify_profile_members(
                parsed_properties=ex.parse_member_property_lines(config.file_path),
                member_ids=topology.member_ids,
                member_incidences=topology.member_incidences,
                node_coordinates=topology.node_coordinates,
                generation_request=request,
            )
            envelopes = {}
            for group, members in profiles.items():
                check_cancel(abort_check)
                if members:
                    envelopes[group] = ex._extract_property_envelope(
                        **shared,
                        member_ids=members,
                        node_ids=ex._node_ids_for_members(topology, members),
                        include_minor_axes=group == "mullion",
                        members_only_displacement=group == "transom",
                    )
        elif flow == "casement":
            casement = ex.extract_casement_profile_envelopes(
                extractor=ex,
                session=session,
                config=config,
                topology=topology,
                member_ids=topology.member_ids,
                selected_load_cases=selected,
                runtime_units=units,
                load_case_names=model.titles,
                generation_request=request,
            )
        check_cancel(abort_check)
        payload = ex._build_grouped_result_payload(
            file_path=config.file_path,
            load_case_mode=mode,
            selected_load_cases=selected,
            moment_mode=config.moment_mode,
            include_combinations=config.include_combinations,
            runtime_units=units,
            file_units=file_units,
            moment_output_unit="kN-m",
            displacement_output_unit="mm",
            envelope=global_envelope,
            extraction_flow=flow,
            profile_envelopes=envelopes,
            profile_member_ids=profiles,
        )
        if casement is not None:
            payload["casement"] = casement
        payload["analysis"].update(
            backend="inhouse",
            solver_version=__version__,
            validation_status="reference_comparison_required",
            diagnostics=result.diagnostics,
        )
        payload["physical_response"] = build_physical_response(
            result, selected, abort_check
        )
        return payload
    finally:
        output.close()


def analyze_file(
    path,
    *,
    flow="standard",
    include_combinations=True,
    generation_request_path=None,
    abort_check=None,
    progress=None,
):
    if progress:
        progress(15, "Parsing model, units and load cases.")
    if generation_request_path:
        import json

        try:
            request = json.loads(
                Path(generation_request_path).read_text(encoding="utf-8-sig")
            )
            if not isinstance(request, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, OSError) as exc:
            raise AnalysisError(
                "INVALID_STD_FILE", f"Invalid generation request JSON: {exc}"
            ) from exc
    model = parse_std(path, abort_check)
    result = solve(model, abort_check, progress)
    config = make_config(
        path, flow, include_combinations, generation_request_path, progress
    )
    payload = build_payload(result, config, abort_check)
    if progress:
        progress(97, "Analysis and profile envelopes complete.")
    return payload
