"""OpenSTAAD automation for extracting maximum bending moment and displacement."""

from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

_PROFILE_CLASSIFIER_DIR = Path(__file__).resolve().parent
if str(_PROFILE_CLASSIFIER_DIR) not in sys.path:
    # Preserve the API package ahead of this directory's legacy app.py when
    # multiprocessing copies sys.path into an independent numerical worker.
    sys.path.append(str(_PROFILE_CLASSIFIER_DIR))

from profile_classifier import (  # noqa: E402
    classify_profile_members,
    load_generation_request,
    parse_member_property_lines,
    resolve_extraction_flow,
)
from casement_extractor import (  # noqa: E402
    build_empty_casement_payload,
    extract_casement_profile_envelopes,
)

try:
    import pythoncom
    import win32com.client
    from win32com.client import VARIANT
except ImportError:  # pragma: no cover - depends on Windows + pywin32
    pythoncom = None
    win32com = None
VARIANT = None


LOGGER = logging.getLogger("staad_max_extractor")
_THREAD_LOCAL = threading.local()
OPENSTAADPY_WHL = Path(
    r"C:\Program Files\Bentley\Engineering\STAAD.Pro 2025\STAAD\OpenSTAADPy\Setup\openstaadpy-25.0.1.1-py3-none-any.whl"
)

FORCE_TO_KN = {
    "KN": 1.0,
    "N": 0.001,
    "MN": 1000.0,
    "DN": 0.01,
    "KIP": 4.4482216152605,
    "LB": 0.0044482216152605,
    "KG": 0.00980665,
    "KGF": 0.00980665,
    "TON": 9.80665,
    "TONNE": 9.80665,
    "T": 9.80665,
}

LENGTH_TO_M = {
    "M": 1.0,
    "METER": 1.0,
    "METRE": 1.0,
    "MET": 1.0,
    "MM": 0.001,
    "MILLIMETER": 0.001,
    "MILLIMETRE": 0.001,
    "CM": 0.01,
    "CENTIMETER": 0.01,
    "CENTIMETRE": 0.01,
    "FT": 0.3048,
    "FEET": 0.3048,
    "FOOT": 0.3048,
    "IN": 0.0254,
    "INCH": 0.0254,
    "DM": 0.1,
    "KM": 1000.0,
}

MOMENT_INDEX = {
    "MX": 3,
    "MY": 4,
    "MZ": 5,
}

FORCE_INDEX = {
    "FX": 0,
    "FY": 1,
    "FZ": 2,
}

# STAAD.Pro 2025's wrapper docs and example disagree on this flag. Empirically, `0`
# matches the printed local member force table for the models we are validating against.
MEMBER_FORCE_LOCAL_FLAG = 0

DISPLACEMENT_INDEX = {
    "X": 0,
    "Y": 1,
    "Z": 2,
}

MEMBER_DEFLECTION_SAMPLE_POINTS = 11
_COORD_EPS = 1e-6
_NUMBER_PATTERN = r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?"


class StaadAutomationError(RuntimeError):
    """Raised when the OpenSTAAD workflow cannot be completed."""


@dataclass(frozen=True)
class DetectedUnits:
    force_unit: str
    length_unit: str

    @property
    def moment_unit(self) -> str:
        return f"{self.force_unit}-{self.length_unit.lower()}"


@dataclass(frozen=True)
class ParsedModelTopology:
    member_ids: list[int]
    node_ids: list[int]
    node_coordinates: dict[int, tuple[float, float, float]] | None = None
    member_incidences: dict[int, tuple[int, int]] | None = None


@dataclass(frozen=True)
class ParsedStdMetadata:
    file_units: DetectedUnits
    available_load_cases: list[int]
    primary_load_cases: list[int]
    topology: ParsedModelTopology
    load_case_names: dict[int, str] | None = None


@dataclass(frozen=True)
class ExtractionConfig:
    file_path: Path
    load_case: int | str | None
    moment_axis: str
    moment_mode: str
    include_combinations: bool
    displacement_axis: str
    source_force_unit: str | None
    source_length_unit: str | None
    moment_output_unit: str
    displacement_output_unit: str
    analysis_timeout_seconds: int
    poll_interval_seconds: float
    launch_wait_seconds: float
    startup_timeout_seconds: int
    attach_timeout_seconds: int
    output_path: Path | None
    progress_callback: Callable[[int, str], None] | None = None
    extraction_flow: str = "standard"
    generation_request_path: Path | None = None


@dataclass
class StaadSession:
    root: Any
    geometry: Any
    output: Any
    load: Any | None = None
    command: Any | None = None
    view: Any | None = None


@dataclass(frozen=True)
class PropertyEnvelopeResults:
    max_bm_major_mz: dict[str, Any] | None
    max_bm_minor_my: dict[str, Any] | None
    bm_minor_my_at_max_major_bm_point: dict[str, Any] | None
    max_sf_major_fy: dict[str, Any] | None
    max_sf_minor_fz_mullion_only: dict[str, Any] | None
    sf_minor_fz_at_max_major_sf_point: dict[str, Any] | None
    max_axial: dict[str, Any]
    max_displacement: dict[str, Any]


def configure_logging(verbose: bool, log_file: Path | None = None, log_append: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, mode="a" if log_append else "w", encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    LOGGER.setLevel(level)
    if log_file is not None:
        LOGGER.info("Writing logs to %s", log_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract maximum bending moment and nodal displacement from a STAAD .std file."
    )
    parser.add_argument("--file", required=True, help="Path to the STAAD .std file")
    parser.add_argument(
        "--loadcase",
        type=str,
        help="Load case number to extract, or 'all' to envelope across every load case in the file",
    )
    parser.add_argument(
        "--moment-axis",
        default="Mz",
        choices=["Mx", "My", "Mz", "mx", "my", "mz"],
        help="Member end moment axis to inspect",
    )
    parser.add_argument(
        "--moment-mode",
        default="end-forces",
        choices=["end-forces", "internal-envelope"],
        help="How to compute bending moment: member end forces or full-member internal envelope",
    )
    parser.add_argument(
        "--include-combinations",
        action="store_true",
        help="When using --loadcase all, include load combinations in the envelope instead of primary load cases only",
    )
    parser.add_argument(
        "--displacement-axis",
        default="RESULTANT",
        choices=["X", "Y", "Z", "R", "RESULTANT", "x", "y", "z", "r", "resultant"],
        help="Node displacement direction to inspect, or RESULTANT for magnitude",
    )
    parser.add_argument(
        "--source-force-unit",
        help="Override the force unit used by the STAAD model (for example KN, N, KIP)",
    )
    parser.add_argument(
        "--source-length-unit",
        help="Override the length unit used by the STAAD model (for example METER, MM, FT)",
    )
    parser.add_argument(
        "--moment-output-unit",
        default="kN-m",
        help="Target unit label for bending moment output",
    )
    parser.add_argument(
        "--displacement-output-unit",
        default="mm",
        help="Target unit label for displacement output",
    )
    parser.add_argument(
        "--analysis-timeout-seconds",
        type=int,
        default=900,
        help="Maximum time to wait for STAAD analysis completion",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=2.0,
        help="Polling interval while waiting for analysis/results",
    )
    parser.add_argument(
        "--launch-wait-seconds",
        type=float,
        default=20.0,
        help="Initial wait after launching STAAD before attach retries begin",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=int,
        default=120,
        help="Maximum time to wait for STAAD startup/openstaadpy attach after launch",
    )
    parser.add_argument(
        "--attach-timeout-seconds",
        type=int,
        default=90,
        help="Maximum time to wait for COM attach once STAAD is running",
    )
    parser.add_argument("--output", help="Optional file path for JSON output")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--log-file", help="Optional file path to also save logs (console output still shown)")
    parser.add_argument("--log-append", action="store_true", help="Append to --log-file instead of overwriting it")
    parser.add_argument(
        "--extraction-flow",
        default="standard",
        help="Extraction flow: standard, fully_unitized, or casement",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> tuple[argparse.Namespace, ExtractionConfig]:
    parser = build_parser()
    args = parser.parse_args(argv)
    file_path = Path(args.file).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else None
    if args.log_file:
        args.log_file = Path(args.log_file).expanduser().resolve()
    config = ExtractionConfig(
        file_path=file_path,
        load_case=_parse_load_case_argument(args.loadcase),
        moment_axis=args.moment_axis.upper(),
        moment_mode=args.moment_mode,
        include_combinations=args.include_combinations,
        displacement_axis=_normalize_displacement_axis_argument(args.displacement_axis),
        source_force_unit=_normalize_force_unit(args.source_force_unit) if args.source_force_unit else None,
        source_length_unit=_normalize_length_unit(args.source_length_unit) if args.source_length_unit else None,
        moment_output_unit=args.moment_output_unit,
        displacement_output_unit=args.displacement_output_unit,
        analysis_timeout_seconds=args.analysis_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        launch_wait_seconds=args.launch_wait_seconds,
        startup_timeout_seconds=args.startup_timeout_seconds,
        attach_timeout_seconds=args.attach_timeout_seconds,
        output_path=output_path,
        extraction_flow=resolve_extraction_flow(args.extraction_flow, None),
    )
    return args, config


def main(argv: Sequence[str] | None = None) -> int:
    args, config = parse_args(argv)
    configure_logging(args.verbose, args.log_file, args.log_append)

    try:
        result = run(config)
    except Exception as exc:  # pragma: no cover - failure path depends on runtime
        LOGGER.exception("STAAD extraction failed: %s", exc)
        error_payload = {
            "file": str(config.file_path),
            "error": str(exc),
        }
        json_text = json.dumps(error_payload, indent=2 if args.pretty else None)
        print(json_text)
        return 1

    json_text = json.dumps(result, indent=2 if args.pretty else None)
    if config.output_path:
        config.output_path.write_text(json_text + "\n", encoding="utf-8")
        LOGGER.info("Saved JSON output to %s", config.output_path)
    print(json_text)
    return 0


def report_progress(config: ExtractionConfig, progress_percent: int, message: str) -> None:
    if config.progress_callback is None:
        return
    try:
        config.progress_callback(progress_percent, message)
    except Exception as exc:
        LOGGER.debug("Progress callback failed: %s", exc)


def run(config: ExtractionConfig) -> dict[str, Any]:
    report_progress(config, 10, "Preparing STAAD input.")
    validate_input_file(config.file_path)
    metadata = parse_std_file_metadata(
        config.file_path,
        source_force_unit=config.source_force_unit,
        source_length_unit=config.source_length_unit,
    )
    report_progress(config, 15, "Parsed STAAD model metadata.")
    file_units = metadata.file_units
    available_load_cases = metadata.available_load_cases
    topology = metadata.topology

    session: StaadSession | None = None
    try:
        LOGGER.info("Connecting to STAAD.Pro via OpenSTAAD COM")
        report_progress(config, 25, "Connecting to STAAD.Pro.")
        session = connect_to_staad(
            config.file_path,
            launch_wait_seconds=config.launch_wait_seconds,
            startup_timeout_seconds=config.startup_timeout_seconds,
            attach_timeout_seconds=config.attach_timeout_seconds,
        )

        LOGGER.info("Opening STAAD file: %s", config.file_path)
        report_progress(config, 30, "Opening STAAD model.")
        open_file(session, config.file_path)

        report_progress(config, 35, "Preparing analysis inputs.")
        runtime_units = detect_runtime_units(session, file_units)
        LOGGER.info(
            "Using source units for conversion: force=%s length=%s (file parse: force=%s length=%s)",
            runtime_units.force_unit,
            runtime_units.length_unit,
            file_units.force_unit,
            file_units.length_unit,
        )
        primary_load_cases = discover_primary_load_cases(
            session,
            config.file_path,
            fallback_primary_load_cases=metadata.primary_load_cases,
        )
        member_ids = get_member_ids(session.geometry, fallback_ids=topology.member_ids)
        node_ids = get_node_ids(session.geometry, fallback_ids=topology.node_ids)
        load_case_names = metadata.load_case_names or {}

        LOGGER.info("Running analysis")
        report_progress(config, 45, "Running STAAD analysis.")
        run_analysis(session, config.analysis_timeout_seconds, config.poll_interval_seconds)
        report_progress(config, 60, "STAAD analysis completed.")

        report_progress(config, 65, "Selecting load cases.")
        selected_load_cases, load_case_mode = resolve_load_case_selection(
            config.load_case,
            available_load_cases,
            primary_load_cases,
            config.include_combinations,
        )
        LOGGER.info("Using load case selection: %s", selected_load_cases)

        if load_case_mode == "single":
            set_current_load_case(session, selected_load_cases[0])

        if load_case_mode == "all":
            LOGGER.info("Extracting bending-moment envelope for MY and MZ across all load cases")
            report_progress(config, 70, "Extracting bending moment results.")
            max_bm = get_bending_moment_envelope(
                session=session,
                load_cases=selected_load_cases,
                moment_axes=["MY", "MZ"],
                moment_mode=config.moment_mode,
                source_units=runtime_units,
                output_unit=config.moment_output_unit,
                fallback_member_ids=topology.member_ids,
                member_ids=member_ids,
            )
            LOGGER.info("Extracting displacement envelope across all load cases")
            report_progress(config, 80, "Extracting displacement results.")
            max_disp = get_displacement_envelope(
                session=session,
                load_cases=selected_load_cases,
                displacement_axis=config.displacement_axis,
                source_units=runtime_units,
                output_unit=config.displacement_output_unit,
                fallback_member_ids=topology.member_ids,
                fallback_node_ids=topology.node_ids,
                member_ids=member_ids,
                node_ids=node_ids,
            )
            LOGGER.info("Extracting axial-force envelope from inner vertical members across all load cases")
            report_progress(config, 87, "Extracting axial force results.")
            max_axial = _extract_max_axial_result(
                session=session,
                load_case_mode=load_case_mode,
                selected_load_cases=selected_load_cases,
                runtime_units=runtime_units,
                topology=topology,
                member_ids=member_ids,
                load_case_names=load_case_names,
            )
            LOGGER.info("Preparing shear-force extraction across all load cases")
            report_progress(config, 92, "Extracting shear force results.")
        else:
            load_case = selected_load_cases[0]
            LOGGER.info("Extracting maximum bending moment")
            report_progress(config, 70, "Extracting bending moment results.")
            max_bm = get_max_bending_moment(
                session=session,
                load_case=load_case,
                moment_axis=config.moment_axis,
                moment_mode=config.moment_mode,
                source_units=runtime_units,
                output_unit=config.moment_output_unit,
                fallback_member_ids=topology.member_ids,
                member_ids=member_ids,
            )

            LOGGER.info("Extracting maximum nodal displacement")
            report_progress(config, 80, "Extracting displacement results.")
            max_disp = get_max_displacement(
                session=session,
                load_case=load_case,
                displacement_axis=config.displacement_axis,
                source_units=runtime_units,
                output_unit=config.displacement_output_unit,
                fallback_member_ids=topology.member_ids,
                fallback_node_ids=topology.node_ids,
                member_ids=member_ids,
                node_ids=node_ids,
            )
            LOGGER.info("Extracting maximum axial force from inner vertical members")
            report_progress(config, 87, "Extracting axial force results.")
            max_axial = _extract_max_axial_result(
                session=session,
                load_case_mode=load_case_mode,
                selected_load_cases=selected_load_cases,
                runtime_units=runtime_units,
                topology=topology,
                member_ids=member_ids,
                load_case_names=load_case_names,
            )
            LOGGER.info("Preparing maximum shear force extraction")
            report_progress(config, 92, "Extracting shear force results.")

        report_progress(config, 95, "Finalizing extracted results.")
        generation_request = load_generation_request(config.generation_request_path)
        extraction_flow = resolve_extraction_flow(config.extraction_flow, generation_request)
        global_envelope = _extract_property_envelope(
            session=session,
            config=config,
            topology=topology,
            member_ids=member_ids,
            node_ids=node_ids,
            load_case_mode=load_case_mode,
            selected_load_cases=selected_load_cases,
            runtime_units=runtime_units,
            load_case_names=load_case_names,
            include_minor_axes=True,
        )

        profile_envelopes: dict[str, PropertyEnvelopeResults] | None = None
        profile_member_ids: dict[str, list[int]] | None = None
        casement_payload: dict[str, Any] | None = None
        if extraction_flow == "fully_unitized":
            report_progress(config, 96, "Classifying profile members.")
            parsed_properties = parse_member_property_lines(config.file_path)
            profile_member_ids = classify_profile_members(
                parsed_properties=parsed_properties,
                member_ids=member_ids,
                member_incidences=topology.member_incidences,
                node_coordinates=topology.node_coordinates,
                generation_request=generation_request,
            )
            profile_envelopes = {}
            for group_name, group_member_ids in profile_member_ids.items():
                group_node_ids = _node_ids_for_members(topology, group_member_ids)
                if not group_node_ids:
                    group_node_ids = node_ids
                profile_envelopes[group_name] = _extract_property_envelope(
                    session=session,
                    config=config,
                    topology=topology,
                    member_ids=group_member_ids,
                    node_ids=group_node_ids,
                    load_case_mode=load_case_mode,
                    selected_load_cases=selected_load_cases,
                    runtime_units=runtime_units,
                    load_case_names=load_case_names,
                    include_minor_axes=group_name == "mullion",
                    members_only_displacement=group_name == "transom",
                )
        elif extraction_flow == "casement":
            report_progress(config, 96, "Extracting Casement profile envelopes.")
            try:
                casement_payload = extract_casement_profile_envelopes(
                    extractor=sys.modules[__name__],
                    session=session,
                    config=config,
                    topology=topology,
                    member_ids=member_ids,
                    selected_load_cases=selected_load_cases,
                    runtime_units=runtime_units,
                    load_case_names=load_case_names,
                    generation_request=generation_request,
                )
            except Exception as exc:
                LOGGER.warning(
                    "[STAAD][CASEMENT] Casement extraction failed soft; returning empty casement object: %s",
                    exc,
                )
                casement_payload = build_empty_casement_payload()

        payload = _build_grouped_result_payload(
            file_path=config.file_path,
            load_case_mode=load_case_mode,
            selected_load_cases=selected_load_cases,
            moment_mode=config.moment_mode,
            include_combinations=config.include_combinations,
            runtime_units=runtime_units,
            file_units=file_units,
            moment_output_unit=config.moment_output_unit,
            displacement_output_unit=config.displacement_output_unit,
            envelope=global_envelope,
            extraction_flow=extraction_flow,
            profile_envelopes=profile_envelopes,
            profile_member_ids=profile_member_ids,
        )
        if extraction_flow == "casement" and casement_payload is not None:
            payload["casement"] = casement_payload
        return payload
    finally:
        release_staad_session(session)


def validate_input_file(file_path: Path) -> None:
    if not file_path.exists():
        raise FileNotFoundError(f"STAAD file not found: {file_path}")
    if file_path.suffix.lower() != ".std":
        raise StaadAutomationError(f"Expected a .std file, received: {file_path.name}")


def connect_to_staad(
    file_path: Path | None = None,
    *,
    launch_wait_seconds: float = 20.0,
    startup_timeout_seconds: int = 120,
    attach_timeout_seconds: int = 90,
) -> StaadSession:
    _ensure_interactive_windows_session()

    openstaadpy_session = _connect_via_openstaadpy(file_path)
    if _session_is_attach_ready(openstaadpy_session):
        LOGGER.info("Connected using Bentley openstaadpy wrapper")
        return openstaadpy_session

    if file_path is not None:
        LOGGER.warning("STAAD.Pro does not appear to be running. Launching STAAD and retrying.")
        launched = launch_staad_application(file_path)
        if launch_wait_seconds > 0:
            LOGGER.info(
                "STAAD launch pid=%s waiting %.1fs for UI initialization before attach",
                launched.pid,
                launch_wait_seconds,
            )
            deadline = time.time() + launch_wait_seconds
            while time.time() < deadline:
                try_accept_postprocessing_dialog()
                time.sleep(1.0)
        openstaadpy_session = wait_for_openstaadpy_connection(
            file_path,
            timeout_seconds=max(5, startup_timeout_seconds),
        )
        if _session_is_attach_ready(openstaadpy_session):
            LOGGER.info("Connected using Bentley openstaadpy wrapper after launching STAAD.Pro")
            return openstaadpy_session
        LOGGER.warning("openstaadpy attach after launch failed. Trying COM fallback.")

    com_timeout_seconds = max(5, attach_timeout_seconds if file_path is not None else min(attach_timeout_seconds, 15))
    com_session = wait_for_com_connection(timeout_seconds=com_timeout_seconds)
    if _session_is_attach_ready(com_session):
        LOGGER.info("Connected to STAAD.Pro via OpenSTAAD COM")
        return com_session

    if win32com is None:
        raise StaadAutomationError(
            "pywin32 is not available. Install requirements.txt and run this on Windows with STAAD.Pro installed."
        )

    raise StaadAutomationError(
        "STAAD.Pro launched, but OpenSTAAD did not expose usable Geometry/Output interfaces. "
        "On Windows EC2 this usually means a Bentley license/sign-in prompt or another modal STAAD dialog "
        "is waiting in the interactive desktop session."
    )


def _ensure_interactive_windows_session() -> None:
    diagnostics = _get_windows_session_diagnostics()
    if diagnostics.get("interactive", True):
        LOGGER.info(
            "Windows session diagnostics: user=%s session_name=%s session_id=%s active_console_session_id=%s",
            diagnostics.get("user"),
            diagnostics.get("session_name"),
            diagnostics.get("session_id"),
            diagnostics.get("active_console_session_id"),
        )
        return

    LOGGER.error(
        "Windows session diagnostics indicate a non-interactive session: user=%s session_name=%s "
        "session_id=%s active_console_session_id=%s reason=%s",
        diagnostics.get("user"),
        diagnostics.get("session_name"),
        diagnostics.get("session_id"),
        diagnostics.get("active_console_session_id"),
        diagnostics.get("reason"),
    )
    raise StaadAutomationError(
        "STAAD.Pro cannot be automated from a non-interactive Windows session. "
        f"Detected session_name={diagnostics.get('session_name')!r}, "
        f"session_id={diagnostics.get('session_id')!r}. "
        "This commonly happens when Windows Task Scheduler is configured with "
        "'Run whether user is logged on or not'. Run the STAAD-hosting task in the "
        "logged-in desktop session instead, for example by selecting "
        "'Run only when user is logged on'."
    )


def _get_windows_session_diagnostics() -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "user": os.environ.get("USERNAME"),
        "session_name": os.environ.get("SESSIONNAME"),
        "session_id": None,
        "active_console_session_id": None,
        "interactive": True,
        "reason": None,
    }
    if sys.platform != "win32":
        return diagnostics

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        session_id = wintypes.DWORD()
        if kernel32.ProcessIdToSessionId(wintypes.DWORD(os.getpid()), ctypes.byref(session_id)):
            diagnostics["session_id"] = int(session_id.value)
        active_console_session_id = kernel32.WTSGetActiveConsoleSessionId()
        if active_console_session_id != 0xFFFFFFFF:
            diagnostics["active_console_session_id"] = int(active_console_session_id)
    except Exception as exc:
        diagnostics["reason"] = f"session-detection-failed: {exc}"
        return diagnostics

    session_name = str(diagnostics.get("session_name") or "").strip().lower()
    session_id = diagnostics.get("session_id")
    if session_name.startswith("services"):
        diagnostics["interactive"] = False
        diagnostics["reason"] = "SESSIONNAME indicates the Services desktop"
    elif session_id == 0:
        diagnostics["interactive"] = False
        diagnostics["reason"] = "process is running in Windows session 0"

    return diagnostics


def launch_staad_application(file_path: Path | None = None) -> subprocess.Popen[Any]:
    executable = find_staad_executable()
    if executable is None:
        raise StaadAutomationError(
            "Could not find Bentley.Staad.exe or SProStaad.exe under the STAAD.Pro 2025 installation."
        )

    _cleanup_stale_staad_before_launch()

    args = [str(executable)]
    if file_path is not None:
        args.append(str(file_path))

    LOGGER.info("Launching STAAD.Pro: %s", executable)
    proc = subprocess.Popen(args, cwd=str(executable.parent))
    LOGGER.info("Launched STAAD.Pro pid=%s", proc.pid)
    return proc


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _cleanup_stale_staad_before_launch() -> None:
    """
    Warm mode reuses a healthy STAAD instance. If attach failed and we reached a
    new launch, any existing STAAD process is stale for this automation run, so
    terminate it first to avoid parallel STAAD instances.
    """
    if sys.platform != "win32" or not _env_flag("KEEP_STAAD_WARM"):
        return

    for image_name in ["Bentley.Staad.exe", "STAADPro.exe", "SProStaad.exe"]:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/IM", image_name],
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.warning("Could not clean stale STAAD process %s before launch: %s", image_name, exc)
            continue

        if result.returncode == 0:
            LOGGER.warning(
                "Terminated stale STAAD process before warm-mode relaunch: image=%s output=%s",
                image_name,
                (result.stdout or "").strip()[:300],
            )


def wait_for_openstaadpy_connection(file_path: Path | None, timeout_seconds: int = 180) -> StaadSession | None:
    deadline = time.time() + timeout_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        session = _connect_via_openstaadpy(file_path)
        if _session_is_attach_ready(session):
            LOGGER.info("openstaadpy attach succeeded attempt=%s", attempt)
            return session
        LOGGER.info("openstaadpy attach pending attempt=%s timeout_remaining=%.1fs", attempt, deadline - time.time())
        if try_accept_postprocessing_dialog():
            LOGGER.info("Sent automatic confirmation to a STAAD dialog while waiting for openstaadpy attach.")
        time.sleep(2.0)
    return None


def find_staad_executable() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Bentley\Engineering\STAAD.Pro 2025\STAAD\Bentley.Staad.exe"),
        Path(r"C:\Program Files\Bentley\Engineering\STAAD.Pro 2025\STAAD\SProStaad\SProStaad.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def open_file(session: StaadSession, file_path: Path) -> None:
    if _is_openstaadpy_session(session):
        current_file = _safe_call(lambda: session.root.GetSTAADFile())
        if current_file and Path(str(current_file)).resolve() == file_path.resolve():
            LOGGER.debug("STAAD file is already active in openstaadpy session: %s", current_file)
            return

    _call_first_success(
        [session.root],
        ["OpenSTAADFile", "OpenSTAADFile2", "OpenFile"],
        [[str(file_path)]],
        error_message="OpenSTAAD could not open the STAAD file.",
    )
    time.sleep(1.0)
    session.geometry = _resolve_child_interface(session.root, ["Geometry", "GetGeometry"], required=False) or session.geometry
    session.output = _resolve_child_interface(session.root, ["Output", "GetOutput"], required=False) or session.output
    session.load = _resolve_child_interface(session.root, ["Load", "GetLoad"], required=False) or session.load
    session.command = _resolve_child_interface(session.root, ["Command", "GetCommand"], required=False) or session.command
    session.view = _resolve_child_interface(session.root, ["View", "GetView"], required=False) or session.view
    session.output = _create_output_interface() or session.output
    _flag_known_methods(session.geometry, GEOMETRY_METHOD_NAMES)
    _flag_known_methods(session.output, OUTPUT_METHOD_NAMES)
    _flag_known_methods(session.command, COMMAND_METHOD_NAMES)
    _flag_known_methods(session.view, VIEW_METHOD_NAMES)


def run_analysis(session: StaadSession, timeout_seconds: int, poll_interval_seconds: float) -> None:
    if _is_openstaadpy_session(session):
        status = session.root.AnalyzeEx(1, 1, 1)
        if isinstance(status, (int, float)) and status < 0:
            raise StaadAutomationError(f"STAAD analysis failed with status code {status}.")
        wait_for_analysis_completion(session, timeout_seconds, poll_interval_seconds)
        return

    targets = [target for target in [session.command, session.root] if target is not None]
    _call_first_success(
        targets,
        ["PerformAnalysis", "Analyze", "RunAnalysis", "AnalyzeModel"],
        [[], [1], [True]],
        error_message="Could not find an OpenSTAAD method to run analysis.",
    )
    wait_for_analysis_completion(session, timeout_seconds, poll_interval_seconds)


def wait_for_analysis_completion(
    session: StaadSession,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _results_ready(session):
            LOGGER.info("Analysis results are available")
            return
        time.sleep(poll_interval_seconds)

    raise StaadAutomationError(
        f"Timed out after {timeout_seconds} seconds waiting for STAAD analysis to complete."
    )


def enter_postprocessing_mode(session: StaadSession) -> None:
    # Legacy UI-only helper. The automated extraction path no longer calls this,
    # because STAAD can show modal warnings for analytical models here.
    LOGGER.debug("Skipping explicit post-processing mode switch.")
    return

    targets = [target for target in [session.view, session.root, session.command] if target is not None]
    try:
        _call_first_success(
            targets,
            [
                "GoToPostProcessingMode",
                "EnterPostProcessingMode",
                "SetPostProcessingMode",
                "SetInterfaceMode",
            ],
            [[], [1], [2], [True]],
            error_message="No post-processing switch method was available.",
        )
    except StaadAutomationError:
        LOGGER.warning(
            "OpenSTAAD post-processing mode switch method was not found; continuing because output calls may still work."
        )
        if try_accept_postprocessing_dialog():
            LOGGER.info("Sent automatic confirmation to the STAAD post-processing dialog.")
            time.sleep(1.0)


def resolve_load_case_selection(
    requested_load_case: int | str | None,
    available_load_cases: list[int],
    primary_load_cases: list[int],
    include_combinations: bool,
) -> tuple[list[int], str]:
    if requested_load_case is None:
        if available_load_cases:
            return [available_load_cases[0]], "single"
        raise StaadAutomationError("No load cases could be detected from the .std file. Pass --loadcase explicitly.")

    if isinstance(requested_load_case, str) and requested_load_case.lower() == "all":
        if include_combinations:
            selected = available_load_cases
        else:
            selected = primary_load_cases
        if not selected:
            raise StaadAutomationError("No load cases could be detected from the .std file.")
        return selected, "all"

    load_case = int(requested_load_case)
    if available_load_cases and load_case not in available_load_cases:
        raise StaadAutomationError(
            f"Invalid load case {load_case}. Available load cases: {available_load_cases}"
        )
    return [load_case], "single"


def set_current_load_case(session: StaadSession, load_case: int) -> None:
    try:
        _call_first_success(
            [session.output, session.root],
            ["SetCurrentLoadCase", "SetActiveLoadCase", "SelectLoadCase"],
            [[load_case]],
            error_message=f"Unable to set current load case to {load_case}.",
        )
    except StaadAutomationError:
        # OpenSTAAD result getters also accept the load case as a direct argument,
        # so GUI load-case activation is helpful but not required.
        LOGGER.warning(
            "Could not activate load case %s in the STAAD UI. Continuing with direct load-case result calls.",
            load_case,
        )


def get_max_bending_moment(
    session: StaadSession,
    load_case: int,
    moment_axis: str,
    moment_mode: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any]:
    if member_ids is None:
        member_ids = get_member_ids(session.geometry, fallback_ids=fallback_member_ids)
    if not member_ids:
        raise StaadAutomationError("No members found in the STAAD model.")

    moment_index = MOMENT_INDEX[moment_axis]
    max_value = -1.0
    max_member_id = None
    max_location = None
    max_station = None
    max_station_unit = None

    for member_id in member_ids:
        candidates = []
        candidate_station_unit = None

        if moment_mode == "internal-envelope":
            member_extremes = get_member_bending_moment_extremes(session.output, member_id, load_case, moment_axis)
            if member_extremes:
                candidates.extend(
                    [
                        ("member-minimum", member_extremes["minimum_value"], member_extremes["minimum_station"]),
                        ("member-maximum", member_extremes["maximum_value"], member_extremes["maximum_station"]),
                    ]
                )
                candidate_station_unit = source_units.length_unit.lower()
        else:
            for end in (0, 1):
                forces = get_member_end_forces(session.output, member_id, end, load_case)
                raw_value = forces[moment_index]
                candidates.append(("start" if end == 0 else "end", raw_value, float(end)))

        for location_label, raw_value, station in candidates:
            LOGGER.debug(
                "Member %s load case %s axis %s raw moment at %s = %.6f (%s)",
                member_id,
                load_case,
                moment_axis,
                location_label,
                raw_value,
                source_units.moment_unit,
            )
            signed_converted = convert_moment_to_kn_m(raw_value, source_units.force_unit, source_units.length_unit)
            converted = abs(signed_converted)
            LOGGER.debug(
                "Converted member %s load case %s axis %s -> signed %.6f %s, absolute %.6f %s",
                member_id,
                load_case,
                moment_axis,
                signed_converted,
                output_unit,
                converted,
                output_unit,
            )
            if converted > max_value:
                max_value = converted
                max_member_id = member_id
                max_location = location_label
                max_station = float(station)
                max_station_unit = candidate_station_unit

    if max_member_id is None or max_location is None:
        raise StaadAutomationError("Unable to extract bending moment results from any STAAD member.")

    return {
        "value": round(max_value, 6),
        "unit": output_unit,
        "member_id": int(max_member_id),
        "location": max_location,
        "load_case": int(load_case),
        "station": round(max_station, 6) if max_station is not None else None,
        "station_unit": max_station_unit,
        "axis": moment_axis,
    }


def _calculate_relative_transom_deflection(
    max_global_disp: float,
    start_disp: float,
    end_disp: float,
) -> float:
    support_disp = min(abs(start_disp), abs(end_disp))
    return abs(max_global_disp) - support_disp


def _relative_transom_deflection_from_supports(
    session: StaadSession,
    incidence: tuple[int, int],
    load_case: int,
    displacement_axis: str,
    source_units: DetectedUnits,
    max_global_disp_mm: float,
) -> float:
    start_node, end_node = incidence
    start_disp_mm = convert_length(
        _node_displacement_component(
            get_node_displacements(session.output, start_node, load_case),
            displacement_axis,
        ),
        source_units.length_unit,
        "MM",
    )
    end_disp_mm = convert_length(
        _node_displacement_component(
            get_node_displacements(session.output, end_node, load_case),
            displacement_axis,
        ),
        source_units.length_unit,
        "MM",
    )
    return _calculate_relative_transom_deflection(max_global_disp_mm, start_disp_mm, end_disp_mm)


def _member_max_span_displacement_mm(
    session: StaadSession,
    member_id: int,
    load_case: int,
    displacement_axis: str,
    source_units: DetectedUnits,
    output_unit: str,
) -> tuple[float | None, float | None]:
    length = _get_member_length(session.geometry, member_id)
    if length is None or length <= 0:
        return None, None

    member_max_value = -1.0
    member_max_station = None
    for station in _sample_stations(length, MEMBER_DEFLECTION_SAMPLE_POINTS):
        raw_value = None
        if displacement_axis == "RESULTANT":
            try:
                abs_displacements = get_member_intermediate_abs_trans_displacements(
                    session.output,
                    member_id,
                    station,
                    load_case,
                )
                raw_value = _member_absolute_displacement_resultant(abs_displacements)
                LOGGER.debug(
                    "Member %s load case %s axis %s raw absolute span displacement at %.6f = %.6f (%s)",
                    member_id,
                    load_case,
                    displacement_axis,
                    station,
                    raw_value,
                    source_units.length_unit,
                )
            except Exception as exc:
                LOGGER.debug(
                    "Member %s load case %s axis %s at %.6f absolute displacement query failed; "
                    "falling back to deflection query: %s",
                    member_id,
                    load_case,
                    displacement_axis,
                    station,
                    exc,
                )

        if raw_value is None:
            try:
                deflection = get_member_intermediate_deflection(session.output, member_id, station, load_case)
            except Exception as exc:
                LOGGER.debug(
                    "Skipping member %s load case %s axis %s at %.6f; intermediate deflection query failed: %s",
                    member_id,
                    load_case,
                    displacement_axis,
                    station,
                    exc,
                )
                continue
            raw_value = _member_deflection_component(deflection, displacement_axis)
        if raw_value is None:
            LOGGER.debug(
                "Skipping member %s load case %s axis %s at %.6f; unsupported deflection tuple: %s",
                member_id,
                load_case,
                displacement_axis,
                station,
                tuple(deflection),
            )
            continue
        if displacement_axis != "RESULTANT":
            LOGGER.debug(
                "Member %s load case %s axis %s raw span deflection at %.6f = %.6f (%s)",
                member_id,
                load_case,
                displacement_axis,
                station,
                raw_value,
                source_units.length_unit,
            )
        converted = convert_length(raw_value, source_units.length_unit, "MM")
        LOGGER.debug(
            "Converted member %s load case %s axis %s at %.6f -> %.6f %s",
            member_id,
            load_case,
            displacement_axis,
            station,
            converted,
            output_unit,
        )
        if converted > member_max_value:
            member_max_value = converted
            member_max_station = station

    if member_max_value < 0:
        return None, None
    return member_max_value, member_max_station


def get_max_displacement(
    session: StaadSession,
    load_case: int,
    displacement_axis: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    fallback_node_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
    node_ids: list[int] | None = None,
    members_only: bool = False,
    member_incidences: dict[int, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    max_value = -1.0
    max_node_id = None
    max_member_id = None
    max_station = None
    max_source = "node"
    selection_global_value: float | None = None
    transom_relative_mode = members_only and member_incidences is not None
    governing_global_mm = -1.0
    governing_relative_mm = -1.0
    governing_member_id: int | None = None
    governing_station: float | None = None

    if not members_only:
        if node_ids is None:
            node_ids = get_node_ids(session.geometry, fallback_ids=fallback_node_ids)
        if not node_ids:
            raise StaadAutomationError("No nodes found in the STAAD model.")

        for node_id in node_ids:
            displacements = get_node_displacements(session.output, node_id, load_case)
            raw_value = _node_displacement_component(displacements, displacement_axis)
            LOGGER.debug(
                "Node %s load case %s axis %s raw displacement = %.6f (%s)",
                node_id,
                load_case,
                displacement_axis,
                raw_value,
                source_units.length_unit,
            )
            converted = convert_length(raw_value, source_units.length_unit, "MM")
            LOGGER.debug(
                "Converted node %s load case %s axis %s -> %.6f %s",
                node_id,
                load_case,
                displacement_axis,
                converted,
                output_unit,
            )
            if converted > max_value:
                max_value = converted
                max_node_id = node_id
                max_member_id = None
                max_station = None
                max_source = "node"

    if displacement_axis in {"Y", "Z", "RESULTANT"}:
        if member_ids is None:
            member_ids = get_member_ids(session.geometry, fallback_ids=fallback_member_ids)
        for member_id in member_ids:
            member_max_value, member_max_station = _member_max_span_displacement_mm(
                session=session,
                member_id=member_id,
                load_case=load_case,
                displacement_axis=displacement_axis,
                source_units=source_units,
                output_unit=output_unit,
            )
            if member_max_value is None:
                continue

            if transom_relative_mode:
                if member_max_value > governing_global_mm:
                    governing_global_mm = member_max_value
                    governing_member_id = member_id
                    governing_station = member_max_station
                    incidence = member_incidences.get(member_id)
                    if incidence is not None:
                        governing_relative_mm = _relative_transom_deflection_from_supports(
                            session=session,
                            incidence=incidence,
                            load_case=load_case,
                            displacement_axis=displacement_axis,
                            source_units=source_units,
                            max_global_disp_mm=member_max_value,
                        )
                    else:
                        governing_relative_mm = member_max_value
                    LOGGER.debug(
                        "Member %s load case %s axis %s relative transom deflection = %.6f %s "
                        "(global max %.6f)",
                        member_id,
                        load_case,
                        displacement_axis,
                        governing_relative_mm,
                        output_unit,
                        member_max_value,
                    )
                continue

            if member_max_value > max_value:
                max_value = member_max_value
                max_node_id = None
                max_member_id = member_id
                max_station = member_max_station
                max_source = "member-span"

        if transom_relative_mode and governing_member_id is not None:
            max_value = governing_relative_mm
            max_node_id = None
            max_member_id = governing_member_id
            max_station = governing_station
            max_source = "member-span"
            selection_global_value = governing_global_mm

    if max_node_id is None and max_member_id is None:
        if members_only:
            raise StaadAutomationError(
                "Unable to extract member-span displacement results from the selected profile members."
            )
        raise StaadAutomationError("Unable to extract displacement results from the STAAD model.")

    result: dict[str, Any] = {
        "value": round(max_value, 6),
        "unit": output_unit,
        "load_case": int(load_case),
        "direction": displacement_axis,
        "source": max_source,
    }
    if max_node_id is not None:
        result["node_id"] = int(max_node_id)
    if max_member_id is not None:
        result["member_id"] = int(max_member_id)
        result["station"] = round(max_station, 6) if max_station is not None else None
        result["station_unit"] = source_units.length_unit.lower()
    if selection_global_value is not None:
        result["selection_global_value"] = round(selection_global_value, 6)
    return result


def get_max_axial_force(
    session: StaadSession,
    load_case: int,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any]:
    if member_ids is None:
        member_ids = get_member_ids(session.geometry, fallback_ids=fallback_member_ids)
    if not member_ids:
        raise StaadAutomationError("No members found in the STAAD model.")

    max_value = -1.0
    max_member_id = None
    max_location = None

    for member_id in member_ids:
        for end in (0, 1):
            forces = get_member_end_forces(session.output, member_id, end, load_case)
            raw_value = forces[FORCE_INDEX["FX"]]
            signed_converted = convert_force_to_kn(raw_value, source_units.force_unit)
            converted = abs(signed_converted)
            location_label = "start" if end == 0 else "end"
            LOGGER.debug(
                "Member %s load case %s raw axial force at %s = %.6f (%s), converted %.6f %s",
                member_id,
                load_case,
                location_label,
                raw_value,
                source_units.force_unit,
                converted,
                output_unit,
            )
            if converted > max_value:
                max_value = converted
                max_member_id = member_id
                max_location = location_label

    if max_member_id is None or max_location is None:
        raise StaadAutomationError("Unable to extract axial force results from any STAAD member.")

    return {
        "value": round(max_value, 6),
        "unit": output_unit,
        "member_id": int(max_member_id),
        "location": max_location,
        "load_case": int(load_case),
        "axis": "FX",
    }


def get_max_shear_force(
    session: StaadSession,
    load_case: int,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any]:
    if member_ids is None:
        member_ids = get_member_ids(session.geometry, fallback_ids=fallback_member_ids)
    if not member_ids:
        raise StaadAutomationError("No members found in the STAAD model.")

    max_value = -1.0
    max_member_id = None
    max_location = None
    max_axis = None

    for member_id in member_ids:
        for end in (0, 1):
            forces = get_member_end_forces(session.output, member_id, end, load_case)
            location_label = "start" if end == 0 else "end"
            for axis in ("FY", "FZ"):
                raw_value = forces[FORCE_INDEX[axis]]
                signed_converted = convert_force_to_kn(raw_value, source_units.force_unit)
                converted = abs(signed_converted)
                LOGGER.debug(
                    "Member %s load case %s raw shear force axis %s at %s = %.6f (%s), converted %.6f %s",
                    member_id,
                    load_case,
                    axis,
                    location_label,
                    raw_value,
                    source_units.force_unit,
                    converted,
                    output_unit,
                )
                if converted > max_value:
                    max_value = converted
                    max_member_id = member_id
                    max_location = location_label
                    max_axis = axis

    if max_member_id is None or max_location is None or max_axis is None:
        raise StaadAutomationError("Unable to extract shear force results from any STAAD member.")

    return {
        "value": round(max_value, 6),
        "unit": output_unit,
        "member_id": int(max_member_id),
        "location": max_location,
        "load_case": int(load_case),
        "axis": max_axis,
    }


def get_max_shear_force_axis(
    session: StaadSession,
    load_case: int,
    shear_axis: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any]:
    if member_ids is None:
        member_ids = get_member_ids(session.geometry, fallback_ids=fallback_member_ids)
    if not member_ids:
        raise StaadAutomationError("No members found in the STAAD model.")

    axis = shear_axis.upper()
    force_index = FORCE_INDEX[axis]
    max_value = -1.0
    max_member_id = None
    max_location = None
    max_end_index = None
    max_station = None
    max_raw_value = None
    max_signed_value = None

    for member_id in member_ids:
        for end in (0, 1):
            forces = get_member_end_forces(session.output, member_id, end, load_case)
            location_label = "start" if end == 0 else "end"
            raw_value = forces[force_index]
            signed_converted = convert_force_to_kn(raw_value, source_units.force_unit)
            converted = abs(signed_converted)
            LOGGER.debug(
                "Member %s load case %s raw shear force axis %s at %s = %.6f (%s), converted %.6f %s",
                member_id,
                load_case,
                axis,
                location_label,
                raw_value,
                source_units.force_unit,
                converted,
                output_unit,
            )
            if converted > max_value:
                max_value = converted
                max_member_id = member_id
                max_location = location_label
                max_end_index = end
                max_station = float(end)
                max_raw_value = float(raw_value)
                max_signed_value = float(signed_converted)

    if max_member_id is None or max_location is None:
        raise StaadAutomationError(f"Unable to extract {axis} shear force results from any STAAD member.")

    return {
        "value": round(max_value, 6),
        "unit": output_unit,
        "member_id": int(max_member_id),
        "location": max_location,
        "station": round(max_station, 6) if max_station is not None else None,
        "station_unit": None,
        "end_index": max_end_index,
        "load_case": int(load_case),
        "axis": axis,
        "signed_value": round(max_signed_value, 6) if max_signed_value is not None else None,
        "original_value": round(max_raw_value, 6) if max_raw_value is not None else None,
        "original_unit": source_units.force_unit,
    }


def get_shear_force_axis_envelope(
    session: StaadSession,
    load_cases: list[int],
    shear_axis: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any]:
    best_result: dict[str, Any] | None = None
    for load_case in load_cases:
        candidate = get_max_shear_force_axis(
            session=session,
            load_case=load_case,
            shear_axis=shear_axis,
            source_units=source_units,
            output_unit=output_unit,
            fallback_member_ids=fallback_member_ids,
            member_ids=member_ids,
        )
        if best_result is None or candidate["value"] > best_result["value"]:
            best_result = candidate
    if best_result is None:
        raise StaadAutomationError(f"Unable to extract {shear_axis} shear force envelope from the STAAD model.")
    return best_result


def _node_ids_for_members(topology: ParsedModelTopology, member_ids: list[int]) -> list[int]:
    if not topology.member_incidences:
        return []
    node_ids: set[int] = set()
    for member_id in member_ids:
        incidence = topology.member_incidences.get(member_id)
        if incidence is None:
            continue
        node_ids.add(incidence[0])
        node_ids.add(incidence[1])
    return sorted(node_ids)


def _extract_property_envelope(
    *,
    session: StaadSession,
    config: ExtractionConfig,
    topology: ParsedModelTopology,
    member_ids: list[int],
    node_ids: list[int],
    load_case_mode: str,
    selected_load_cases: list[int],
    runtime_units: DetectedUnits,
    load_case_names: dict[int, str],
    include_minor_axes: bool,
    members_only_displacement: bool = False,
) -> PropertyEnvelopeResults:
    if not member_ids:
        raise StaadAutomationError("No members found for property envelope extraction.")

    if load_case_mode == "all":
        max_bm = get_bending_moment_envelope(
            session=session,
            load_cases=selected_load_cases,
            moment_axes=["MY", "MZ"] if include_minor_axes else ["MZ"],
            moment_mode=config.moment_mode,
            source_units=runtime_units,
            output_unit=config.moment_output_unit,
            fallback_member_ids=topology.member_ids,
            member_ids=member_ids,
        )
        max_disp = get_displacement_envelope(
            session=session,
            load_cases=selected_load_cases,
            displacement_axis=config.displacement_axis,
            source_units=runtime_units,
            output_unit=config.displacement_output_unit,
            fallback_member_ids=topology.member_ids,
            fallback_node_ids=topology.node_ids,
            member_ids=member_ids,
            node_ids=node_ids,
            members_only=members_only_displacement,
            member_incidences=topology.member_incidences if members_only_displacement else None,
        )
        max_axial = _extract_max_axial_result(
            session=session,
            load_case_mode=load_case_mode,
            selected_load_cases=selected_load_cases,
            runtime_units=runtime_units,
            topology=topology,
            member_ids=member_ids,
            load_case_names=load_case_names,
        )
    else:
        load_case = selected_load_cases[0]
        max_bm = get_max_bending_moment(
            session=session,
            load_case=load_case,
            moment_axis=config.moment_axis,
            moment_mode=config.moment_mode,
            source_units=runtime_units,
            output_unit=config.moment_output_unit,
            fallback_member_ids=topology.member_ids,
            member_ids=member_ids,
        )
        max_disp = get_max_displacement(
            session=session,
            load_case=load_case,
            displacement_axis=config.displacement_axis,
            source_units=runtime_units,
            output_unit=config.displacement_output_unit,
            fallback_member_ids=topology.member_ids,
            fallback_node_ids=topology.node_ids,
            member_ids=member_ids,
            node_ids=node_ids,
            members_only=members_only_displacement,
            member_incidences=topology.member_incidences if members_only_displacement else None,
        )
        max_axial = _extract_max_axial_result(
            session=session,
            load_case_mode=load_case_mode,
            selected_load_cases=selected_load_cases,
            runtime_units=runtime_units,
            topology=topology,
            member_ids=member_ids,
            load_case_names=load_case_names,
        )

    max_bm_major_mz = _resolve_major_bending_moment_mz(
        session=session,
        max_bending_moment=max_bm,
        load_case_mode=load_case_mode,
        load_case=selected_load_cases[0] if load_case_mode == "single" else None,
        moment_axis=config.moment_axis,
        moment_mode=config.moment_mode,
        source_units=runtime_units,
        output_unit=config.moment_output_unit,
        fallback_member_ids=topology.member_ids,
        member_ids=member_ids,
    )
    max_bm_minor_my = None
    bm_minor_my_at_max_major_bm_point = None
    if include_minor_axes:
        max_bm_minor_my = _resolve_bending_moment_axis(
            session=session,
            max_bending_moment=max_bm,
            load_case_mode=load_case_mode,
            load_case=selected_load_cases[0] if load_case_mode == "single" else None,
            current_moment_axis=config.moment_axis,
            target_axis="MY",
            moment_mode=config.moment_mode,
            source_units=runtime_units,
            output_unit=config.moment_output_unit,
            fallback_member_ids=topology.member_ids,
            member_ids=member_ids,
        )
        bm_minor_my_at_max_major_bm_point = _safe_get_axis_result_at_point(
            session=session,
            point_result=max_bm_major_mz,
            axis="MY",
            source_units=runtime_units,
            output_unit=config.moment_output_unit,
        )

    max_sf_major_fy = _safe_get_shear_force_axis_envelope(
        session=session,
        load_cases=selected_load_cases,
        shear_axis="FY",
        source_units=runtime_units,
        output_unit="kN",
        fallback_member_ids=topology.member_ids,
        member_ids=member_ids,
    )
    max_sf_major_fy_result = _enrich_shear_result_metadata(
        max_sf_major_fy,
        session.geometry,
        topology,
        load_case_names,
    )

    max_sf_minor_fz_mullion_only = None
    sf_minor_fz_at_max_major_sf_point = None
    if include_minor_axes:
        sf_minor_fz_at_max_major_sf_point = _safe_get_axis_result_at_fixed_point_envelope(
            session=session,
            point_result=max_sf_major_fy,
            axis="FZ",
            load_cases=selected_load_cases,
            source_units=runtime_units,
            output_unit="kN",
            load_case_names=load_case_names,
        )
        minor_fz_member_ids = _get_vertical_member_ids(session.geometry, member_ids, topology) or member_ids
        max_sf_minor_fz = _safe_get_shear_force_axis_envelope(
            session=session,
            load_cases=selected_load_cases,
            shear_axis="FZ",
            source_units=runtime_units,
            output_unit="kN",
            fallback_member_ids=topology.member_ids,
            member_ids=minor_fz_member_ids,
        )
        max_sf_minor_fz_mullion_only = _enrich_shear_result_metadata(
            max_sf_minor_fz,
            session.geometry,
            topology,
            load_case_names,
            forced_member_category="mullion",
        )

    return PropertyEnvelopeResults(
        max_bm_major_mz=max_bm_major_mz,
        max_bm_minor_my=max_bm_minor_my,
        bm_minor_my_at_max_major_bm_point=bm_minor_my_at_max_major_bm_point,
        max_sf_major_fy=max_sf_major_fy_result,
        max_sf_minor_fz_mullion_only=max_sf_minor_fz_mullion_only,
        sf_minor_fz_at_max_major_sf_point=sf_minor_fz_at_max_major_sf_point,
        max_axial=max_axial,
        max_displacement=max_disp,
    )


def _build_properties_block(
    envelope: PropertyEnvelopeResults,
    *,
    moment_output_unit: str,
    displacement_output_unit: str,
    include_minor_axes: bool,
) -> dict[str, Any]:
    bending_major = _clean_engineering_result(envelope.max_bm_major_mz)
    shear_major = _clean_engineering_result(envelope.max_sf_major_fy)
    properties: dict[str, Any] = {
        "bending_moment": {
            "unit": moment_output_unit,
            "major": bending_major,
        },
        "shear_force": {
            "unit": "kN",
            "major": shear_major,
        },
        "axial_force": {
            "unit": "kN",
            **(_clean_engineering_result(envelope.max_axial) or {}),
        },
        "displacement": {
            "unit": displacement_output_unit,
            **(_clean_engineering_result(envelope.max_displacement) or {}),
        },
    }

    if include_minor_axes:
        properties["bending_moment"]["minor"] = {
            "axis": "MY",
            "max": _clean_engineering_result(envelope.max_bm_minor_my, drop_keys={"axis"}),
            "at_major_governing_point": _clean_at_major_bending_result(
                envelope.bm_minor_my_at_max_major_bm_point,
                envelope.max_bm_major_mz,
            ),
        }
        properties["shear_force"]["minor"] = {
            "axis": "FZ",
            "max_on_mullions": _clean_engineering_result(
                envelope.max_sf_minor_fz_mullion_only,
                drop_keys={"axis"},
            ),
            "at_major_governing_point": _clean_at_major_shear_result(
                envelope.sf_minor_fz_at_max_major_sf_point,
                envelope.max_sf_major_fy,
            ),
        }

    return properties


def _resolve_major_bending_moment_mz(
    session: StaadSession,
    max_bending_moment: dict[str, Any],
    load_case_mode: str,
    load_case: int | None,
    moment_axis: str,
    moment_mode: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    return _resolve_bending_moment_axis(
        session=session,
        max_bending_moment=max_bending_moment,
        load_case_mode=load_case_mode,
        load_case=load_case,
        current_moment_axis=moment_axis,
        target_axis="MZ",
        moment_mode=moment_mode,
        source_units=source_units,
        output_unit=output_unit,
        fallback_member_ids=fallback_member_ids,
        member_ids=member_ids,
    )


def _resolve_bending_moment_axis(
    session: StaadSession,
    max_bending_moment: dict[str, Any],
    load_case_mode: str,
    load_case: int | None,
    current_moment_axis: str,
    target_axis: str,
    moment_mode: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    axis = target_axis.upper()
    if load_case_mode == "all":
        axis_result = max_bending_moment.get(axis)
        return dict(axis_result) if isinstance(axis_result, dict) else None

    if current_moment_axis.upper() == axis and "value" in max_bending_moment:
        return dict(max_bending_moment)

    if load_case is None:
        return None

    try:
        return get_max_bending_moment(
            session=session,
            load_case=load_case,
            moment_axis=axis,
            moment_mode=moment_mode,
            source_units=source_units,
            output_unit=output_unit,
            fallback_member_ids=fallback_member_ids,
            member_ids=member_ids,
        )
    except Exception as exc:
        LOGGER.warning("Unable to extract explicit %s bending moment result: %s", axis, exc)
        return None


def _safe_get_shear_force_axis_envelope(
    session: StaadSession,
    load_cases: list[int],
    shear_axis: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    try:
        return get_shear_force_axis_envelope(
            session=session,
            load_cases=load_cases,
            shear_axis=shear_axis,
            source_units=source_units,
            output_unit=output_unit,
            fallback_member_ids=fallback_member_ids,
            member_ids=member_ids,
        )
    except Exception as exc:
        LOGGER.warning("Unable to extract explicit %s shear companion result: %s", shear_axis.upper(), exc)
        return None


def _build_grouped_result_payload(
    file_path: Path,
    load_case_mode: str,
    selected_load_cases: list[int],
    moment_mode: str,
    include_combinations: bool,
    runtime_units: DetectedUnits,
    file_units: DetectedUnits,
    moment_output_unit: str,
    displacement_output_unit: str,
    envelope: PropertyEnvelopeResults | None = None,
    extraction_flow: str = "standard",
    profile_envelopes: dict[str, PropertyEnvelopeResults] | None = None,
    profile_member_ids: dict[str, list[int]] | None = None,
    max_bm_major_mz: dict[str, Any] | None = None,
    max_bm_minor_my: dict[str, Any] | None = None,
    bm_minor_my_at_max_major_bm_point: dict[str, Any] | None = None,
    max_sf_major_fy: dict[str, Any] | None = None,
    max_sf_minor_fz_mullion_only: dict[str, Any] | None = None,
    sf_minor_fz_at_max_major_sf_point: dict[str, Any] | None = None,
    max_axial: dict[str, Any] | None = None,
    max_displacement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if envelope is None:
        if max_bm_major_mz is None or max_axial is None or max_displacement is None:
            raise TypeError("envelope or legacy envelope fields are required")
        envelope = PropertyEnvelopeResults(
            max_bm_major_mz=max_bm_major_mz,
            max_bm_minor_my=max_bm_minor_my,
            bm_minor_my_at_max_major_bm_point=bm_minor_my_at_max_major_bm_point,
            max_sf_major_fy=max_sf_major_fy,
            max_sf_minor_fz_mullion_only=max_sf_minor_fz_mullion_only,
            sf_minor_fz_at_max_major_sf_point=sf_minor_fz_at_max_major_sf_point,
            max_axial=max_axial,
            max_displacement=max_displacement,
        )
    elif max_bm_major_mz is not None:
        envelope = PropertyEnvelopeResults(
            max_bm_major_mz=max_bm_major_mz,
            max_bm_minor_my=max_bm_minor_my,
            bm_minor_my_at_max_major_bm_point=bm_minor_my_at_max_major_bm_point,
            max_sf_major_fy=max_sf_major_fy,
            max_sf_minor_fz_mullion_only=max_sf_minor_fz_mullion_only,
            sf_minor_fz_at_max_major_sf_point=sf_minor_fz_at_max_major_sf_point,
            max_axial=max_axial or {},
            max_displacement=max_displacement or {},
        )

    properties = _build_properties_block(
        envelope,
        moment_output_unit=moment_output_unit,
        displacement_output_unit=displacement_output_unit,
        include_minor_axes=True,
    )

    result_properties: dict[str, Any] = dict(properties)
    payload: dict[str, Any] = {
        "file": str(file_path),
        "analysis": {
            "load_case_mode": load_case_mode,
            "load_cases": selected_load_cases,
            "moment_mode": moment_mode,
            "include_combinations": include_combinations,
        },
        "units": {
            "output": {
                "force": "kN",
                "length": displacement_output_unit,
                "moment": moment_output_unit,
            },
            "staad_detected": {
                "force": runtime_units.force_unit,
                "length": runtime_units.length_unit,
                "moment": runtime_units.moment_unit,
            },
            "file_detected": {
                "force": file_units.force_unit,
                "length": file_units.length_unit,
                "moment": file_units.moment_unit,
            },
        },
        "properties": result_properties,
    }

    if extraction_flow == "fully_unitized" and profile_envelopes is not None:
        result_properties["global"] = properties
        for group_name, group_envelope in profile_envelopes.items():
            result_properties[group_name] = _build_properties_block(
                group_envelope,
                moment_output_unit=moment_output_unit,
                displacement_output_unit=displacement_output_unit,
                include_minor_axes=group_name == "mullion",
            )
        member_counts = {
            group_name: len(group_member_ids)
            for group_name, group_member_ids in (profile_member_ids or {}).items()
        }
        payload["profiles"] = {
            "extraction_flow": extraction_flow,
            "member_counts": member_counts,
        }

    return payload


def _clean_engineering_result(
    result: dict[str, Any] | None,
    drop_keys: set[str] | None = None,
) -> dict[str, Any] | None:
    if result is None:
        return None

    allowed_keys = {
        "value",
        "axis",
        "direction",
        "member_id",
        "member_type",
        "node_id",
        "source",
        "location",
        "station",
        "station_unit",
        "load_case",
        "load_case_name",
        "governing_end",
        "governing_station",
    }
    blocked_keys = {"unit", "signed_value", "original_value", "original_unit", "end_index", "note"}
    if drop_keys:
        blocked_keys = blocked_keys | drop_keys

    return _strip_none_values(
        {
            key: value
            for key, value in result.items()
            if key in allowed_keys and key not in blocked_keys
        }
    )


def _clean_at_major_bending_result(
    minor_at_major: dict[str, Any] | None,
    major: dict[str, Any] | None,
) -> dict[str, Any] | None:
    cleaned = _clean_engineering_result(minor_at_major, drop_keys={"axis"})
    if cleaned is None:
        return None
    if major is not None:
        cleaned["reference_major_value"] = major.get("value")
        cleaned["reference_major_load_case"] = major.get("load_case")
    return _strip_none_values(cleaned)


def _clean_at_major_shear_result(
    minor_at_major: dict[str, Any] | None,
    major: dict[str, Any] | None,
) -> dict[str, Any] | None:
    cleaned = _clean_engineering_result(minor_at_major, drop_keys={"axis", "load_case", "load_case_name"})
    if cleaned is None:
        return None

    governing_load_case = minor_at_major.get("governing_load_case_for_fz")
    governing_load_case_name = minor_at_major.get("governing_load_case_name_for_fz")
    cleaned["governing_load_case"] = governing_load_case
    cleaned["governing_load_case_name"] = governing_load_case_name
    cleaned["reference_major_load_case"] = (
        minor_at_major.get("reference_major_sf_load_case")
        if minor_at_major is not None
        else None
    )
    cleaned["reference_major_value"] = (
        minor_at_major.get("reference_major_sf_value")
        if minor_at_major is not None
        else None
    )
    if major is not None and "member_type" not in cleaned:
        cleaned["member_type"] = major.get("member_type")
    return _strip_none_values(cleaned)


def _strip_none_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _safe_get_axis_result_at_point(
    session: StaadSession,
    point_result: dict[str, Any] | None,
    axis: str,
    source_units: DetectedUnits,
    output_unit: str,
) -> dict[str, Any] | None:
    if point_result is None:
        return None
    try:
        return get_axis_result_at_point(
            session=session,
            point_result=point_result,
            axis=axis,
            source_units=source_units,
            output_unit=output_unit,
        )
    except Exception as exc:
        LOGGER.warning(
            "Unable to extract %s companion result at member %s load case %s location %s: %s",
            axis.upper(),
            point_result.get("member_id"),
            point_result.get("load_case"),
            point_result.get("location"),
            exc,
        )
        return None


def _safe_get_axis_result_at_fixed_point_envelope(
    session: StaadSession,
    point_result: dict[str, Any] | None,
    axis: str,
    load_cases: list[int],
    source_units: DetectedUnits,
    output_unit: str,
    load_case_names: dict[int, str],
) -> dict[str, Any] | None:
    if point_result is None:
        return None

    best_result: dict[str, Any] | None = None
    for load_case in load_cases:
        point_for_load_case = dict(point_result)
        point_for_load_case["load_case"] = load_case
        try:
            candidate = get_axis_result_at_point(
                session=session,
                point_result=point_for_load_case,
                axis=axis,
                source_units=source_units,
                output_unit=output_unit,
            )
        except Exception as exc:
            LOGGER.warning(
                "Unable to extract %s at fixed member %s location %s for load case %s: %s",
                axis.upper(),
                point_result.get("member_id"),
                point_result.get("location"),
                load_case,
                exc,
            )
            continue

        if best_result is None or candidate["value"] > best_result["value"]:
            best_result = candidate

    if best_result is None:
        return None

    governing_load_case = best_result.get("load_case")
    result = dict(best_result)
    result["governing_load_case_for_fz"] = governing_load_case
    result["governing_load_case_name_for_fz"] = load_case_names.get(int(governing_load_case)) if governing_load_case is not None else None
    result["reference_major_sf_load_case"] = point_result.get("load_case")
    result["reference_major_sf_value"] = point_result.get("value")
    result["note"] = (
        "Calculated at the max major FY point, with FZ governed by the maximum absolute value "
        "at that fixed point across all selected load cases."
    )
    return result


def _enrich_shear_result_metadata(
    result: dict[str, Any] | None,
    geometry: Any,
    topology: ParsedModelTopology,
    load_case_names: dict[int, str],
    forced_member_category: str | None = None,
) -> dict[str, Any] | None:
    if result is None:
        return None

    enriched = dict(result)
    member_id = enriched.get("member_id")
    load_case = enriched.get("load_case")
    if member_id is not None:
        enriched["member_type"] = forced_member_category or _get_member_category(
            geometry,
            int(member_id),
            topology,
        )
    else:
        enriched["member_type"] = forced_member_category

    enriched["load_case_name"] = load_case_names.get(int(load_case)) if load_case is not None else None
    enriched.setdefault("station", None)
    enriched.setdefault("station_unit", None)
    if "end_index" in enriched:
        enriched["governing_end"] = enriched.get("location")
    elif enriched.get("location") in {"start", "end"}:
        enriched["governing_end"] = enriched.get("location")
    else:
        enriched["governing_station"] = enriched.get("station")
    return enriched


def _extract_max_axial_result(
    *,
    session: StaadSession,
    load_case_mode: str,
    selected_load_cases: list[int],
    runtime_units: DetectedUnits,
    topology: ParsedModelTopology,
    member_ids: list[int],
    load_case_names: dict[int, str],
) -> dict[str, Any]:
    axial_member_ids = _get_inner_vertical_member_ids(session.geometry, member_ids, topology)
    if not axial_member_ids:
        LOGGER.warning(
            "Could not classify any inner vertical members for axial force. Returning empty axial result."
        )
        return {}

    if load_case_mode == "all":
        max_axial = get_axial_force_envelope(
            session=session,
            load_cases=selected_load_cases,
            source_units=runtime_units,
            output_unit="kN",
            fallback_member_ids=topology.member_ids,
            member_ids=axial_member_ids,
        )
    else:
        max_axial = get_max_axial_force(
            session=session,
            load_case=selected_load_cases[0],
            source_units=runtime_units,
            output_unit="kN",
            fallback_member_ids=topology.member_ids,
            member_ids=axial_member_ids,
        )

    enriched = _enrich_shear_result_metadata(
        max_axial,
        session.geometry,
        topology,
        load_case_names,
        forced_member_category="inner_mullion",
    )
    return enriched or {}


def _get_member_category(
    geometry: Any,
    member_id: int,
    topology: ParsedModelTopology,
) -> str | None:
    if _is_vertical_member(geometry, member_id, topology):
        return "mullion"

    coordinates = _get_member_endpoint_coordinates(geometry, member_id, topology)
    if coordinates is None:
        return None
    return "horizontal"


def _filter_vertical_member_ids(
    geometry: Any,
    member_ids: list[int],
    topology: ParsedModelTopology,
) -> list[int]:
    return [
        member_id
        for member_id in member_ids
        if _is_vertical_member(geometry, member_id, topology)
    ]


def _get_vertical_member_ids(
    geometry: Any,
    member_ids: list[int],
    topology: ParsedModelTopology,
) -> list[int]:
    vertical_member_ids = _filter_vertical_member_ids(geometry, member_ids, topology)
    if vertical_member_ids:
        LOGGER.info(
            "Restricting minor-axis maximum shear force to %d vertical member(s).",
            len(vertical_member_ids),
        )
        return vertical_member_ids

    LOGGER.warning(
        "Could not classify any vertical members for minor-axis maximum shear force. Returning null for max_sf_minor_fz."
    )
    return []


def _coordinates_are_equal(a: float, b: float) -> bool:
    return abs(a - b) <= _COORD_EPS


def _get_vertical_member_horizontal_midpoint(
    geometry: Any,
    member_id: int,
    topology: ParsedModelTopology,
) -> tuple[float, float] | None:
    coordinates = _get_member_endpoint_coordinates(geometry, member_id, topology)
    if coordinates is None:
        return None

    start, end = coordinates
    return ((start[0] + end[0]) / 2.0, (start[2] + end[2]) / 2.0)


def _get_inner_vertical_member_ids(
    geometry: Any,
    member_ids: list[int],
    topology: ParsedModelTopology,
) -> list[int]:
    vertical_member_ids = _filter_vertical_member_ids(geometry, member_ids, topology)
    if not vertical_member_ids:
        return []

    positioned_members: list[tuple[int, float, float]] = []
    for member_id in vertical_member_ids:
        midpoint = _get_vertical_member_horizontal_midpoint(geometry, member_id, topology)
        if midpoint is None:
            continue
        positioned_members.append((member_id, midpoint[0], midpoint[1]))

    if not positioned_members:
        LOGGER.warning(
            "Could not resolve horizontal positions for vertical members. Returning empty inner vertical set."
        )
        return []

    xs = [mid_x for _, mid_x, _ in positioned_members]
    zs = [mid_z for _, _, mid_z in positioned_members]
    min_x = min(xs)
    max_x = max(xs)
    min_z = min(zs)
    max_z = max(zs)
    span_x = max_x - min_x
    span_z = max_z - min_z

    inner_vertical_member_ids: list[int] = []
    for member_id, mid_x, mid_z in positioned_members:
        at_peripheral_x = span_x > _COORD_EPS and (
            _coordinates_are_equal(mid_x, min_x) or _coordinates_are_equal(mid_x, max_x)
        )
        at_peripheral_z = span_z > _COORD_EPS and (
            _coordinates_are_equal(mid_z, min_z) or _coordinates_are_equal(mid_z, max_z)
        )
        if at_peripheral_x or at_peripheral_z:
            continue
        inner_vertical_member_ids.append(member_id)

    if inner_vertical_member_ids:
        LOGGER.info(
            "Restricting axial force to %d inner vertical member(s), excluding %d peripheral vertical member(s).",
            len(inner_vertical_member_ids),
            len(positioned_members) - len(inner_vertical_member_ids),
        )
        return inner_vertical_member_ids

    LOGGER.warning(
        "All %d vertical member(s) are classified as peripheral. Returning empty inner vertical set.",
        len(positioned_members),
    )
    return []


def _is_vertical_member(geometry: Any, member_id: int, topology: ParsedModelTopology) -> bool:
    coordinates = _get_member_endpoint_coordinates(geometry, member_id, topology)
    if coordinates is None:
        return False

    start, end = coordinates
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    abs_x = abs(dx)
    abs_y = abs(dy)
    abs_z = abs(dz)
    horizontal = max(abs_x, abs_z)

    return abs_y > 0.0 and abs_y >= horizontal


def _get_member_endpoint_coordinates(
    geometry: Any,
    member_id: int,
    topology: ParsedModelTopology,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    incidence = _get_member_incidence(geometry, member_id, topology)
    if incidence is None:
        return None

    start_node, end_node = incidence
    start = _get_node_coordinates(geometry, start_node, topology)
    end = _get_node_coordinates(geometry, end_node, topology)
    if start is None or end is None:
        return None
    return start, end


def _get_member_incidence(
    geometry: Any,
    member_id: int,
    topology: ParsedModelTopology,
) -> tuple[int, int] | None:
    if topology.member_incidences and member_id in topology.member_incidences:
        return topology.member_incidences[member_id]

    methods = ["GetMemberIncidence", "GetBeamIncidence", "GetMemberNodes", "GetBeamNodes"]
    arg_sets = [
        [member_id],
        [member_id, _make_long_array_buffer(2)],
        [member_id, _make_python_long_array(2)],
    ]
    try:
        values = _call_array_method(geometry, methods, arg_sets, minimum_length=2)
    except StaadAutomationError:
        return None
    return int(values[0]), int(values[1])


def _get_node_coordinates(
    geometry: Any,
    node_id: int,
    topology: ParsedModelTopology,
) -> tuple[float, float, float] | None:
    if topology.node_coordinates and node_id in topology.node_coordinates:
        return topology.node_coordinates[node_id]

    methods = ["GetNodeCoordinates", "GetJointCoordinates", "GetNodeCoord", "GetJointCoord"]
    arg_sets = [
        [node_id],
        [node_id, _make_double_array_buffer(3)],
        [node_id, _make_python_double_array(3)],
    ]
    try:
        values = _call_array_method(geometry, methods, arg_sets, minimum_length=3)
    except StaadAutomationError:
        return None
    return float(values[0]), float(values[1]), float(values[2])


def get_axis_result_at_point(
    session: StaadSession,
    point_result: dict[str, Any],
    axis: str,
    source_units: DetectedUnits,
    output_unit: str,
) -> dict[str, Any]:
    axis = axis.upper()
    member_id = int(point_result["member_id"])
    load_case = int(point_result["load_case"])
    forces = get_member_forces_at_result_point(session.output, point_result)
    raw_value = forces[_force_result_index(axis)]

    if axis in MOMENT_INDEX:
        signed_converted = convert_moment_to_kn_m(raw_value, source_units.force_unit, source_units.length_unit)
        original_unit = source_units.moment_unit
    else:
        signed_converted = convert_force_to_kn(raw_value, source_units.force_unit)
        original_unit = source_units.force_unit

    result: dict[str, Any] = {
        "value": round(abs(signed_converted), 6),
        "unit": output_unit,
        "member_id": member_id,
        "location": point_result.get("location"),
        "load_case": load_case,
        "axis": axis,
        "signed_value": round(signed_converted, 6),
        "original_value": round(float(raw_value), 6),
        "original_unit": original_unit,
    }
    if "station" in point_result:
        result["station"] = point_result.get("station")
        result["station_unit"] = point_result.get("station_unit")
    if "end_index" in point_result:
        result["end_index"] = point_result.get("end_index")
    return result


def _force_result_index(axis: str) -> int:
    if axis in FORCE_INDEX:
        return FORCE_INDEX[axis]
    if axis in MOMENT_INDEX:
        return MOMENT_INDEX[axis]
    raise StaadAutomationError(f"Unsupported member force axis: {axis}")


def get_member_forces_at_result_point(output: Any, point_result: dict[str, Any]) -> list[float]:
    member_id = int(point_result["member_id"])
    load_case = int(point_result["load_case"])
    location = str(point_result.get("location") or "").lower()

    if location in ("start", "end"):
        end = 0 if location == "start" else 1
        return get_member_end_forces(output, member_id, end, load_case)

    station = point_result.get("station")
    if station is None:
        raise StaadAutomationError("Result point does not include a station for intermediate force lookup.")

    return get_member_intermediate_forces_at_distance(output, member_id, float(station), load_case)


def get_bending_moment_envelope(
    session: StaadSession,
    load_cases: list[int],
    moment_axes: list[str],
    moment_mode: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {}
    for axis in moment_axes:
        best_result: dict[str, Any] | None = None
        for load_case in load_cases:
            candidate = get_max_bending_moment(
                session=session,
                load_case=load_case,
                moment_axis=axis,
                moment_mode=moment_mode,
                source_units=source_units,
                output_unit=output_unit,
                fallback_member_ids=fallback_member_ids,
                member_ids=member_ids,
            )
            if best_result is None or candidate["value"] > best_result["value"]:
                best_result = candidate
        if best_result is not None:
            envelope[axis] = best_result
    if not envelope:
        raise StaadAutomationError("Unable to extract bending moment envelope from the STAAD model.")
    return envelope


def get_displacement_envelope(
    session: StaadSession,
    load_cases: list[int],
    displacement_axis: str,
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    fallback_node_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
    node_ids: list[int] | None = None,
    members_only: bool = False,
    member_incidences: dict[int, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    best_result: dict[str, Any] | None = None
    best_selection_value = -1.0
    for load_case in load_cases:
        candidate = get_max_displacement(
            session=session,
            load_case=load_case,
            displacement_axis=displacement_axis,
            source_units=source_units,
            output_unit=output_unit,
            fallback_member_ids=fallback_member_ids,
            fallback_node_ids=fallback_node_ids,
            member_ids=member_ids,
            node_ids=node_ids,
            members_only=members_only,
            member_incidences=member_incidences,
        )
        if members_only and member_incidences is not None:
            selection_value = float(candidate.get("selection_global_value", -1.0))
        else:
            selection_value = float(candidate["value"])
        if best_result is None or selection_value > best_selection_value:
            best_result = candidate
            best_selection_value = selection_value
    if best_result is None:
        raise StaadAutomationError("Unable to extract displacement envelope from the STAAD model.")
    return best_result


def get_axial_force_envelope(
    session: StaadSession,
    load_cases: list[int],
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any]:
    best_result: dict[str, Any] | None = None
    for load_case in load_cases:
        candidate = get_max_axial_force(
            session=session,
            load_case=load_case,
            source_units=source_units,
            output_unit=output_unit,
            fallback_member_ids=fallback_member_ids,
            member_ids=member_ids,
        )
        if best_result is None or candidate["value"] > best_result["value"]:
            best_result = candidate
    if best_result is None:
        raise StaadAutomationError("Unable to extract axial force envelope from the STAAD model.")
    return best_result


def get_shear_force_envelope(
    session: StaadSession,
    load_cases: list[int],
    source_units: DetectedUnits,
    output_unit: str,
    fallback_member_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> dict[str, Any]:
    best_result: dict[str, Any] | None = None
    for load_case in load_cases:
        candidate = get_max_shear_force(
            session=session,
            load_case=load_case,
            source_units=source_units,
            output_unit=output_unit,
            fallback_member_ids=fallback_member_ids,
            member_ids=member_ids,
        )
        if best_result is None or candidate["value"] > best_result["value"]:
            best_result = candidate
    if best_result is None:
        raise StaadAutomationError("Unable to extract shear force envelope from the STAAD model.")
    return best_result


def _select_governing_bending_moment(max_bending_moment: dict[str, Any]) -> dict[str, Any]:
    if "value" in max_bending_moment:
        return dict(max_bending_moment)

    candidates = [
        value for value in max_bending_moment.values() if isinstance(value, dict) and "value" in value
    ]
    if not candidates:
        raise StaadAutomationError("Unable to determine governing bending moment result.")

    best_candidate = max(candidates, key=lambda candidate: float(candidate["value"]))
    return dict(best_candidate)


def get_member_ids(geometry: Any, fallback_ids: list[int] | None = None) -> list[int]:
    direct_ids = _safe_geometry_list(geometry, ["GetBeamList", "GetMemberList", "BeamList"])
    if direct_ids:
        return direct_ids

    count = None
    try:
        count = int(
            _call_first_success(
                [geometry],
                ["GetMemberCount", "GetBeamCount"],
                [[]],
                error_message="Unable to read member count from STAAD/OpenSTAAD.",
            )
        )
    except StaadAutomationError:
        if fallback_ids:
            LOGGER.warning(
                "Could not read member count from OpenSTAAD. Falling back to member IDs parsed from the .std file."
            )
            return fallback_ids
        raise
    if count <= 0:
        return []

    ids = _try_get_id_list(
        geometry,
        ["GetMemberList", "GetBeamList", "BeamList", "GetAllMembers", "GetAllBeams"],
        count,
    )
    if ids:
        return ids

    if fallback_ids:
        LOGGER.warning(
            "OpenSTAAD did not return a member list. Falling back to member IDs parsed from the .std file."
        )
        return fallback_ids

    return list(range(1, count + 1))


def get_node_ids(geometry: Any, fallback_ids: list[int] | None = None) -> list[int]:
    direct_ids = _safe_geometry_list(geometry, ["GetNodeList", "NodeList", "GetJointList"])
    if direct_ids:
        return direct_ids

    count = None
    try:
        count = int(
            _call_first_success(
                [geometry],
                ["GetNodeCount", "GetJointCount"],
                [[]],
                error_message="Unable to read node count from STAAD/OpenSTAAD.",
            )
        )
    except StaadAutomationError:
        if fallback_ids:
            LOGGER.warning(
                "Could not read node count from OpenSTAAD. Falling back to node IDs parsed from the .std file."
            )
            return fallback_ids
        raise
    if count <= 0:
        return []

    ids = _try_get_id_list(
        geometry,
        ["GetNodeList", "NodeList", "GetJointList", "GetAllNodes", "GetAllJoints"],
        count,
    )
    if ids:
        return ids

    if fallback_ids:
        LOGGER.warning(
            "OpenSTAAD did not return a node list. Falling back to node IDs parsed from the .std file."
        )
        return fallback_ids

    return list(range(1, count + 1))


def get_member_end_forces(output: Any, member_id: int, end: int, load_case: int) -> list[float]:
    if _looks_like_openstaadpy_output(output):
        return [float(value) for value in output.GetMemberEndForces(member_id, end, load_case, MEMBER_FORCE_LOCAL_FLAG)]

    methods = ["GetMemberEndForces", "GetBeamEndForces"]
    arg_sets = [
        [member_id, end],
        [member_id, end, load_case],
        [member_id, end, load_case, MEMBER_FORCE_LOCAL_FLAG],
        [member_id, end, load_case, 0],
        [member_id, end, _make_double_array_buffer(6)],
        [member_id, end, _make_python_double_array(6)],
        [member_id, end, load_case, _make_double_array_buffer(6)],
        [member_id, end, load_case, _make_python_double_array(6)],
        [member_id, end, load_case, _make_double_array_buffer(6), MEMBER_FORCE_LOCAL_FLAG],
        [member_id, end, load_case, _make_double_array_buffer(6), 0],
        [member_id, end, load_case, _make_python_double_array(6), MEMBER_FORCE_LOCAL_FLAG],
        [member_id, end, load_case, _make_python_double_array(6), 0],
    ]
    values = _call_array_method(output, methods, arg_sets, minimum_length=6)
    return values


def get_member_intermediate_forces_at_distance(
    output: Any,
    member_id: int,
    distance: float,
    load_case: int,
) -> list[float]:
    if _looks_like_openstaadpy_output(output):
        values = _extract_numeric_sequence(
            output.GetIntermediateMemberForcesAtDistance(member_id, distance, load_case)
        )
        if len(values) < 6:
            raise StaadAutomationError("OpenSTAAD returned fewer than six intermediate member force values.")
        return [float(value) for value in values]

    methods = ["GetIntermediateMemberForcesAtDistance"]
    arg_sets = [
        [member_id, distance, load_case],
        [member_id, distance, load_case, _make_double_array_buffer(6)],
        [member_id, distance, load_case, _make_python_double_array(6)],
    ]
    values = _call_array_method(output, methods, arg_sets, minimum_length=6)
    return values


def get_member_bending_moment_extremes(
    output: Any,
    member_id: int,
    load_case: int,
    moment_axis: str,
) -> dict[str, float] | None:
    axis = moment_axis.upper()
    method = _get_com_member(output, "GetMinMaxBendingMoment")
    if method is None:
        return None

    try:
        result = method(member_id, axis, load_case)
    except Exception:
        return None

    values = _extract_numeric_sequence(result)
    if len(values) < 4:
        return None

    return {
        "minimum_value": float(values[0]),
        "minimum_station": float(values[1]),
        "maximum_value": float(values[2]),
        "maximum_station": float(values[3]),
    }


def get_member_intermediate_deflection(output: Any, member_id: int, distance: float, load_case: int) -> tuple[float, ...]:
    if _looks_like_openstaadpy_output(output):
        values = _extract_numeric_sequence(output.GetIntermediateDeflectionAtDistance(member_id, distance, load_case))
        if len(values) < 2:
            raise StaadAutomationError(
                "OpenSTAAD returned fewer than two values for intermediate member deflection."
            )
        return tuple(float(value) for value in values)

    methods = ["GetIntermediateDeflectionAtDistance"]
    arg_sets = [
        [member_id, distance, load_case],
        [member_id, distance, load_case, _make_double_array_buffer(1), _make_double_array_buffer(1)],
        [member_id, distance, load_case, _make_python_double_array(1), _make_python_double_array(1)],
    ]
    values = _call_array_method(output, methods, arg_sets, minimum_length=2)
    return tuple(float(value) for value in values)


def get_member_intermediate_abs_trans_displacements(
    output: Any,
    member_id: int,
    distance: float,
    load_case: int,
) -> tuple[float, ...]:
    if _looks_like_openstaadpy_output(output):
        values = _extract_numeric_sequence(
            output.GetIntermediateMemberAbsTransDisplacements(member_id, distance, load_case)
        )
        if len(values) < 3:
            raise StaadAutomationError(
                "OpenSTAAD returned fewer than three values for intermediate member absolute displacement."
            )
        return tuple(float(value) for value in values)

    methods = ["GetIntermediateMemberAbsTransDisplacements"]
    arg_sets = [
        [member_id, distance, load_case],
        [member_id, distance, load_case, _make_double_array_buffer(6)],
        [member_id, distance, load_case, _make_python_double_array(6)],
        [member_id, distance, load_case, _make_double_array_buffer(3)],
        [member_id, distance, load_case, _make_python_double_array(3)],
    ]
    values = _call_array_method(output, methods, arg_sets, minimum_length=3)
    return tuple(float(value) for value in values)


def get_node_displacements(output: Any, node_id: int, load_case: int) -> list[float]:
    if _looks_like_openstaadpy_output(output):
        return [float(value) for value in output.GetNodeDisplacements(node_id, load_case)]

    methods = ["GetNodeDisplacements", "GetJointDisplacements"]
    arg_sets = [
        [node_id],
        [node_id, load_case],
        [node_id, _make_double_array_buffer(6)],
        [node_id, _make_python_double_array(6)],
        [node_id, load_case, _make_double_array_buffer(6)],
        [node_id, load_case, _make_python_double_array(6)],
        [node_id, load_case, _make_double_array_buffer(3)],
        [node_id, load_case, _make_python_double_array(3)],
    ]
    values = _call_array_method(output, methods, arg_sets, minimum_length=3)
    return values


def _get_member_length(geometry: Any, member_id: int) -> float | None:
    method = _get_com_member(geometry, "GetBeamLength")
    if method is None:
        return None
    try:
        value = method(member_id)
    except Exception:
        return None
    try:
        return float(value)
    except Exception:
        values = _extract_numeric_sequence(value)
        return float(values[0]) if values else None


def _sample_stations(length: float, count: int) -> list[float]:
    if count <= 0:
        return []
    if count == 1:
        return [round(length / 2.0, 6)]
    # Node displacements already cover the two member ends; sample only interior stations
    # to avoid endpoint query failures seen in some OpenSTAAD builds.
    step = length / float(count + 1)
    return [round(step * index, 6) for index in range(1, count + 1)]


def _normalize_displacement_axis_argument(value: str) -> str:
    normalized = value.strip().upper()
    if normalized == "R":
        return "RESULTANT"
    return normalized


def _node_displacement_component(displacements: Sequence[float], displacement_axis: str) -> float:
    values = [float(value) for value in displacements]
    if displacement_axis == "RESULTANT":
        return math.sqrt(sum(value * value for value in values[:3]))
    return abs(values[DISPLACEMENT_INDEX[displacement_axis]])


def _member_deflection_component(deflection: Sequence[float], displacement_axis: str) -> float | None:
    values = [float(value) for value in deflection]
    if not values:
        return None

    if displacement_axis == "RESULTANT":
        if len(values) >= 3:
            return math.sqrt(sum(value * value for value in values[:3]))
        if len(values) == 2:
            return math.sqrt(values[0] * values[0] + values[1] * values[1])
        return abs(values[0])

    if len(values) >= 3:
        return abs(values[DISPLACEMENT_INDEX[displacement_axis]])

    if len(values) == 2 and displacement_axis in {"Y", "Z"}:
        component_index = 0 if displacement_axis == "Y" else 1
        return abs(values[component_index])

    return None


def _member_absolute_displacement_resultant(displacements: Sequence[float]) -> float | None:
    values = [float(value) for value in displacements]
    if len(values) < 3:
        return None
    return math.sqrt(sum(value * value for value in values[:3]))


def detect_units(
    file_path: Path,
    source_force_unit: str | None,
    source_length_unit: str | None,
) -> DetectedUnits:
    if source_force_unit and source_length_unit:
        return DetectedUnits(force_unit=source_force_unit, length_unit=source_length_unit)

    text = file_path.read_text(encoding="utf-8", errors="ignore")
    detected_force, detected_length = _detect_units_from_std_history(text)

    if source_force_unit is not None:
        detected_force = source_force_unit
    if source_length_unit is not None:
        detected_length = source_length_unit

    if detected_force is None:
        detected_force = "KN"
        LOGGER.warning("Could not detect force unit from %s. Defaulting to KN.", file_path.name)
    if detected_length is None:
        detected_length = "METER"
        LOGGER.warning("Could not detect length unit from %s. Defaulting to METER.", file_path.name)

    return DetectedUnits(force_unit=detected_force, length_unit=detected_length)


def parse_std_file_metadata(
    file_path: Path,
    source_force_unit: str | None = None,
    source_length_unit: str | None = None,
) -> ParsedStdMetadata:
    current_force: str | None = None
    current_length: str | None = None
    units_at_analysis: tuple[str | None, str | None] | None = None
    primary_cases: set[int] = set()
    combination_cases: set[int] = set()
    load_case_names: dict[int, str] = {}
    node_ids: list[int] = []
    member_ids: list[int] = []
    node_coordinates: dict[int, tuple[float, float, float]] = {}
    member_incidences: dict[int, tuple[int, int]] = {}
    section: str | None = None

    def close_section_if_needed(upper: str) -> None:
        nonlocal section
        if not upper:
            return
        if upper == "JOINT COORDINATES":
            section = "nodes"
            return
        if upper == "MEMBER INCIDENCES":
            section = "members"
            return
        if section is None:
            return
        if upper.startswith("DEFINE ") or upper.startswith("END ") or upper.startswith("UNIT "):
            section = None
            return
        if upper.startswith("LOAD ") or upper.startswith("SELFWEIGHT"):
            section = None
            return
        if upper in {
            "CONSTANTS",
            "SUPPORTS",
            "MEMBER PROPERTY AMERICAN",
            "MEMBER PROPERTY",
            "MEMBER RELEASE",
            "MEMBER LOAD",
            "FLOOR LOAD",
            "PERFORM ANALYSIS",
            "FINISH",
        }:
            section = None

    with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            upper = line.upper()

            if upper.startswith("UNIT "):
                parsed_force, parsed_length = _parse_unit_statement(upper)
                if parsed_force is not None:
                    current_force = parsed_force
                if parsed_length is not None:
                    current_length = parsed_length

            if units_at_analysis is None and upper.startswith("PERFORM ANALYSIS"):
                units_at_analysis = (current_force, current_length)

            if upper.startswith("LOAD "):
                if upper.startswith("LOAD COMB "):
                    match = re.match(r"^LOAD\s+COMB\s+(\d+)\b", upper)
                    if match:
                        load_case_id = int(match.group(1))
                        combination_cases.add(load_case_id)
                        title = _parse_load_case_title(line)
                        if title:
                            load_case_names[load_case_id] = title
                else:
                    match = re.match(r"^LOAD\s+(\d+)\b", upper)
                    if match:
                        load_case_id = int(match.group(1))
                        primary_cases.add(load_case_id)
                        title = _parse_load_case_title(line)
                        if title:
                            load_case_names[load_case_id] = title

            close_section_if_needed(upper)
            if section == "nodes":
                for match in re.finditer(r"(\d+)\s+({0})\s+({0})\s+({0})\s*;?".format(_NUMBER_PATTERN), line):
                    node_ids.append(int(match.group(1)))
                    node_coordinates[int(match.group(1))] = (
                        float(match.group(2)),
                        float(match.group(3)),
                        float(match.group(4)),
                    )
            elif section == "members":
                for match in re.finditer(r"(\d+)\s+(\d+)\s+(\d+)\s*;?", line):
                    member_ids.append(int(match.group(1)))
                    member_incidences[int(match.group(1))] = (int(match.group(2)), int(match.group(3)))

    detected_force, detected_length = units_at_analysis or (current_force, current_length)
    if source_force_unit is not None:
        detected_force = source_force_unit
    if source_length_unit is not None:
        detected_length = source_length_unit

    if detected_force is None:
        detected_force = "KN"
        LOGGER.warning("Could not detect force unit from %s. Defaulting to KN.", file_path.name)
    if detected_length is None:
        detected_length = "METER"
        LOGGER.warning("Could not detect length unit from %s. Defaulting to METER.", file_path.name)

    topology = ParsedModelTopology(
        member_ids=sorted(set(member_ids)),
        node_ids=sorted(set(node_ids)),
        node_coordinates=node_coordinates,
        member_incidences=member_incidences,
    )
    return ParsedStdMetadata(
        file_units=DetectedUnits(force_unit=detected_force, length_unit=detected_length),
        available_load_cases=sorted(primary_cases | combination_cases),
        primary_load_cases=sorted(primary_cases),
        topology=topology,
        load_case_names=load_case_names,
    )


def _parse_load_case_title(line: str) -> str | None:
    match = re.search(r"\bTITLE\b\s+(.+)$", line, flags=re.IGNORECASE)
    if not match:
        return None
    title = match.group(1).strip()
    return title or None


def detect_runtime_units(session: StaadSession, fallback_units: DetectedUnits) -> DetectedUnits:
    base_unit = _query_base_unit(session)
    if base_unit == "ENGLISH":
        LOGGER.info("OpenSTAAD base unit detected as English; using KIP and IN for result conversion.")
        return DetectedUnits(force_unit="KIP", length_unit="IN")
    if base_unit == "METRIC":
        LOGGER.info("OpenSTAAD base unit detected as Metric; using KN and METER for result conversion.")
        return DetectedUnits(force_unit="KN", length_unit="METER")

    force_unit = _query_session_unit(session, ["GetInputUnitForForce"]) or fallback_units.force_unit
    length_unit = _query_session_unit(session, ["GetInputUnitForLength"]) or fallback_units.length_unit

    if force_unit == fallback_units.force_unit and length_unit == fallback_units.length_unit:
        LOGGER.debug(
            "Runtime unit query fell back to parsed file units force=%s length=%s",
            fallback_units.force_unit,
            fallback_units.length_unit,
        )

    return DetectedUnits(force_unit=force_unit, length_unit=length_unit)


def _query_base_unit(session: StaadSession) -> str | None:
    candidates = [session.root, session.command]
    for target in candidates:
        if target is None:
            continue
        method = _get_com_member(target, "GetBaseUnit")
        if method is None:
            continue
        try:
            value = method()
        except Exception:
            continue
        if value is None:
            continue
        normalized = _normalize_base_unit(value)
        if normalized is not None:
            return normalized
    return None


def _query_session_unit(session: StaadSession, method_names: list[str]) -> str | None:
    candidates = [session.root, session.load, session.output, session.command]
    for target in candidates:
        if target is None:
            continue
        for method_name in method_names:
            method = _get_com_member(target, method_name)
            if method is None:
                continue
            try:
                value = method()
            except Exception:
                continue
            if value is None:
                continue
            if "Force" in method_name:
                normalized = _normalize_force_unit(str(value))
                if normalized in FORCE_TO_KN:
                    return normalized
            else:
                normalized = _normalize_length_unit(str(value))
                if normalized in LENGTH_TO_M:
                    return normalized
    return None


def _normalize_base_unit(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = _normalize_unit_key(value)
        if normalized in {"ENGLISH", "METRIC"}:
            return normalized
        return None
    if isinstance(value, (int, float)):
        if int(value) == 1:
            return "ENGLISH"
        if int(value) == 2:
            return "METRIC"
    return None


def _detect_units_from_std_history(text: str) -> tuple[str | None, str | None]:
    current_force: str | None = None
    current_length: str | None = None
    units_at_analysis: tuple[str | None, str | None] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("UNIT "):
            parsed_force, parsed_length = _parse_unit_statement(line)
            if parsed_force is not None:
                current_force = parsed_force
            if parsed_length is not None:
                current_length = parsed_length
            continue
        if units_at_analysis is None and line.upper().startswith("PERFORM ANALYSIS"):
            units_at_analysis = (current_force, current_length)

    if units_at_analysis is not None:
        return units_at_analysis
    return current_force, current_length


def _parse_unit_statement(line: str) -> tuple[str | None, str | None]:
    tokens = re.findall(r"[A-Z]+", line.upper())
    force_unit: str | None = None
    length_unit: str | None = None
    for token in tokens[1:]:
        normalized_force = _normalize_force_unit(token)
        normalized_length = _normalize_length_unit(token)
        if force_unit is None and normalized_force in FORCE_TO_KN:
            force_unit = normalized_force
        if length_unit is None and normalized_length in LENGTH_TO_M:
            length_unit = normalized_length
    return force_unit, length_unit


def discover_load_cases_from_std(file_path: Path) -> list[int]:
    return parse_std_file_metadata(file_path).available_load_cases


def discover_primary_load_cases_from_std(file_path: Path) -> list[int]:
    return parse_std_file_metadata(file_path).primary_load_cases


def discover_primary_load_cases(
    session: StaadSession,
    file_path: Path,
    fallback_primary_load_cases: list[int] | None = None,
) -> list[int]:
    if session.load is not None:
        primary_cases = _safe_call(lambda: session.load.GetPrimaryLoadCaseNumbers())
        sequence = _extract_numeric_sequence(primary_cases)
        if sequence:
            return [int(value) for value in sequence]

    if _safe_call(lambda: getattr(session.root, "Load", None)) is not None:
        load = _safe_call(lambda: session.root.Load)
        if load is not None:
            primary_cases = _safe_call(lambda: load.GetPrimaryLoadCaseNumbers())
            sequence = _extract_numeric_sequence(primary_cases)
            if sequence:
                return [int(value) for value in sequence]

    LOGGER.warning(
        "Could not read primary load cases from OpenSTAAD. Falling back to parsing the .std file."
    )
    if fallback_primary_load_cases is not None:
        return fallback_primary_load_cases
    return discover_primary_load_cases_from_std(file_path)


def _discover_load_cases_from_std(text: str) -> tuple[list[int], list[int]]:
    primary_cases: set[int] = set()
    combination_cases: set[int] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip().upper()
        if not line.startswith("LOAD "):
            continue
        if line.startswith("LOAD COMB "):
            match = re.match(r"^LOAD\s+COMB\s+(\d+)\b", line)
            if match:
                combination_cases.add(int(match.group(1)))
            continue
        match = re.match(r"^LOAD\s+(\d+)\b", line)
        if match:
            primary_cases.add(int(match.group(1)))
    return sorted(primary_cases), sorted(combination_cases)


def parse_model_topology(file_path: Path) -> ParsedModelTopology:
    return parse_std_file_metadata(file_path).topology


def convert_moment_to_kn_m(value: float, force_unit: str, length_unit: str) -> float:
    force_factor = FORCE_TO_KN.get(_normalize_force_unit(force_unit))
    length_factor = LENGTH_TO_M.get(_normalize_length_unit(length_unit))
    if force_factor is None or length_factor is None:
        raise StaadAutomationError(
            f"Unsupported source units for moment conversion: force={force_unit}, length={length_unit}"
        )
    return value * force_factor * length_factor


def convert_force_to_kn(value: float, force_unit: str) -> float:
    force_factor = FORCE_TO_KN.get(_normalize_force_unit(force_unit))
    if force_factor is None:
        raise StaadAutomationError(f"Unsupported source units for force conversion: force={force_unit}")
    return value * force_factor


def convert_length(value: float, source_length_unit: str, target_length_unit: str) -> float:
    source_factor = LENGTH_TO_M.get(_normalize_length_unit(source_length_unit))
    target_factor = LENGTH_TO_M.get(_normalize_length_unit(target_length_unit))
    if source_factor is None or target_factor is None:
        raise StaadAutomationError(
            f"Unsupported length conversion from {source_length_unit} to {target_length_unit}"
        )
    return value * source_factor / target_factor


def _results_ready(session: StaadSession) -> bool:
    checks = [
        (session.output, "AreResultsAvailable", []),
        (session.output, "IsPostProcessingReady", []),
        (session.root, "AreResultsAvailable", []),
    ]

    for target, method_name, args in checks:
        method = _get_com_member(target, method_name)
        if method is None:
            continue
        try:
            result = method(*args)
        except Exception:
            continue
        if isinstance(result, bool):
            return result
        if isinstance(result, (int, float)):
            return bool(result)

    for target in [session.command, session.root]:
        if target is None:
            continue
        for method_name in ["IsAnalyzing", "GetIsAnalyzing"]:
            method = _get_com_member(target, method_name)
            if method is None:
                continue
            try:
                result = method()
            except Exception:
                continue
            if isinstance(result, bool):
                return not result

    return False


def _resolve_child_interface(root: Any, names: list[str], required: bool = True) -> Any | None:
    for name in names:
        try:
            candidate = getattr(root, name)
        except Exception:
            continue
        try:
            child = candidate() if callable(candidate) else candidate
        except TypeError:
            child = candidate
        except Exception:
            continue
        if child is not None:
            return child

    if required:
        raise StaadAutomationError(f"OpenSTAAD interface not available: {', '.join(names)}")
    return None


def _call_first_success(
    targets: Iterable[Any],
    method_names: list[str],
    arg_sets: list[list[Any]],
    error_message: str,
) -> Any:
    errors: list[str] = []
    for target in targets:
        if target is None:
            continue
        for method_name in method_names:
            method = _get_com_member(target, method_name)
            if method is None:
                continue
            for args in arg_sets:
                try:
                    return method(*args)
                except Exception as exc:
                    errors.append(f"{method_name}{tuple(_safe_repr(arg) for arg in args)} -> {exc}")

    if errors:
        LOGGER.debug("OpenSTAAD method attempts failed:\n%s", "\n".join(errors))
    raise StaadAutomationError(error_message)


def _call_array_method(
    target: Any,
    method_names: list[str],
    arg_sets: list[list[Any]],
    minimum_length: int,
) -> list[float]:
    errors: list[str] = []
    for method_name in method_names:
        method = _get_com_member(target, method_name)
        if method is None:
            continue
        for args in arg_sets:
            try:
                result = method(*args)
                values = _extract_numeric_sequence(result)
                if len(values) >= minimum_length:
                    return values
                for arg in reversed(args):
                    values = _extract_numeric_sequence(arg)
                    if len(values) >= minimum_length:
                        return values
            except Exception as exc:
                errors.append(f"{method_name}{tuple(_safe_repr(arg) for arg in args)} -> {exc}")

    if errors:
        LOGGER.debug("Array-returning OpenSTAAD calls failed:\n%s", "\n".join(errors))
    raise StaadAutomationError(
        f"Unable to extract array results using methods: {', '.join(method_names)}"
    )


def _try_get_id_list(target: Any, method_names: list[str], count: int) -> list[int]:
    for method_name in method_names:
        method = _get_com_member(target, method_name)
        if method is None:
            continue
        try:
            result = method()
        except Exception:
            result = None
        values = _extract_numeric_sequence(result)
        if values:
            return [int(value) for value in values[:count]]

        buffer = _make_long_array_buffer(count)
        try:
            result = method(buffer)
        except Exception:
            continue
        values = _extract_numeric_sequence(result)
        if len(values) >= count:
            return [int(value) for value in values[:count]]
        values = _extract_numeric_sequence(buffer)
        if len(values) >= count:
            return [int(value) for value in values[:count]]
    return []


def _extract_numeric_sequence(value: Any) -> list[float]:
    if value is None:
        return []

    raw = getattr(value, "value", value)
    if isinstance(raw, (list, tuple)):
        return [float(item) for item in raw]

    if hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes, dict)):
        try:
            return [float(item) for item in list(raw)]
        except Exception:
            return []

    if isinstance(raw, str):
        matches = re.findall(r"-?\d+(?:\.\d+)?", raw)
        return [float(match) for match in matches]

    return []


def _make_double_array_buffer(size: int) -> Any:
    if pythoncom is not None and VARIANT is not None:
        return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0] * size)
    return [0.0] * size


def _make_python_double_array(size: int) -> Any:
    return array("d", [0.0] * size)


def _make_long_array_buffer(size: int) -> Any:
    if pythoncom is not None and VARIANT is not None:
        return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_ARRAY | pythoncom.VT_I4, [0] * size)
    return [0] * size


def _make_python_long_array(size: int) -> Any:
    return array("l", [0] * size)


def _create_output_interface() -> Any | None:
    if win32com is None:
        return None
    for prog_id in ["OpenSTAAD.Output.1", "OpenSTAAD.Output"]:
        try:
            output = win32com.client.Dispatch(prog_id)
        except Exception:
            continue
        _flag_known_methods(output, OUTPUT_METHOD_NAMES)
        return output
    return None


def _connect_via_com() -> StaadSession | None:
    if win32com is None:
        return None

    if pythoncom is not None and not getattr(_THREAD_LOCAL, "com_initialized", False):
        pythoncom.CoInitialize()
        _THREAD_LOCAL.com_initialized = True

    try:
        root = win32com.client.Dispatch("StaadPro.OpenSTAAD")
    except Exception:
        return None

    _flag_known_methods(root, ROOT_METHOD_NAMES)
    geometry = _resolve_child_interface(root, ["Geometry", "GetGeometry"], required=False)
    output = _resolve_child_interface(root, ["Output", "GetOutput"], required=False)
    command = _resolve_child_interface(root, ["Command", "GetCommand"], required=False)
    view = _resolve_child_interface(root, ["View", "GetView"], required=False)

    # Bentley's Python examples commonly attach to the running STAAD instance with
    # GetActiveObject. Some machines expose sub-interfaces only on that active object.
    if geometry is None or output is None:
        LOGGER.warning(
            "Dispatch connected, but Geometry/Output were not exposed. Trying GetActiveObject fallback."
        )
        try:
            active_root = win32com.client.GetActiveObject("StaadPro.OpenSTAAD")
        except Exception:
            active_root = None
        if active_root is not None:
            root = active_root
            _flag_known_methods(root, ROOT_METHOD_NAMES)
            geometry = _resolve_child_interface(root, ["Geometry", "GetGeometry"], required=False)
            output = _resolve_child_interface(root, ["Output", "GetOutput"], required=False)
            command = _resolve_child_interface(root, ["Command", "GetCommand"], required=False)
            view = _resolve_child_interface(root, ["View", "GetView"], required=False)

    standalone_output = _create_output_interface()
    if geometry is None and _target_has_any_method(root, GEOMETRY_METHOD_NAMES):
        geometry = root
    if output is None:
        output = standalone_output
    if output is None and _target_has_any_method(root, OUTPUT_METHOD_NAMES):
        output = root

    _flag_known_methods(geometry, GEOMETRY_METHOD_NAMES)
    _flag_known_methods(output, OUTPUT_METHOD_NAMES)
    _flag_known_methods(command, COMMAND_METHOD_NAMES)
    _flag_known_methods(view, VIEW_METHOD_NAMES)

    if geometry is None or output is None:
        return None

    load = _resolve_child_interface(root, ["Load", "GetLoad"], required=False)
    return StaadSession(root=root, geometry=geometry, output=output, load=load, command=command, view=view)


def wait_for_com_connection(timeout_seconds: int = 60) -> StaadSession | None:
    deadline = time.time() + timeout_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        session = _connect_via_com()
        if _session_is_attach_ready(session):
            LOGGER.info("COM attach succeeded attempt=%s", attempt)
            return session
        LOGGER.info("COM attach pending attempt=%s timeout_remaining=%.1fs", attempt, deadline - time.time())
        if try_accept_postprocessing_dialog():
            LOGGER.info("Sent automatic confirmation to a STAAD dialog while waiting for COM attach.")
        time.sleep(2.0)
    return None


def release_staad_session(session: StaadSession | None) -> None:
    """
    Release COM references aggressively to reduce cross-job leakage in the worker process.
    """
    if session is not None:
        for attr in ["view", "command", "load", "output", "geometry", "root"]:
            try:
                setattr(session, attr, None)
            except Exception:
                continue
    gc.collect()
    if pythoncom is not None and getattr(_THREAD_LOCAL, "com_initialized", False):
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        finally:
            _THREAD_LOCAL.com_initialized = False


def _connect_via_openstaadpy(file_path: Path | None = None) -> StaadSession | None:
    module = _import_openstaadpy_module()
    if module is None:
        return None

    try:
        if file_path is not None:
            LOGGER.debug("Trying openstaadpy.connect(%s)", file_path)
            staad = module.connect(str(file_path))
        else:
            LOGGER.debug("Trying openstaadpy.connect()")
            staad = module.connect()
    except Exception as exc:
        LOGGER.debug("openstaadpy connect() failed: %s", exc)
        return None

    if staad is None:
        return None

    geometry = _safe_call(lambda: staad.Geometry)
    output = _safe_call(lambda: staad.Output)
    load = _safe_call(lambda: staad.Load)
    view = _safe_call(lambda: staad.View)
    return StaadSession(root=staad, geometry=geometry, output=output, load=load, command=staad, view=view)


def _import_openstaadpy_module() -> Any | None:
    try:
        from openstaadpy import os_analytical  # type: ignore

        return os_analytical
    except Exception:
        pass

    if OPENSTAADPY_WHL.exists():
        wheel_path = str(OPENSTAADPY_WHL)
        if wheel_path not in sys.path:
            sys.path.append(wheel_path)
        try:
            from openstaadpy import os_analytical  # type: ignore

            return os_analytical
        except Exception as exc:
            LOGGER.debug("Importing openstaadpy from STAAD wheel failed: %s", exc)
            return None

    return None


def _looks_like_openstaadpy_output(output: Any) -> bool:
    return output is not None and hasattr(output, "GetMemberEndForces") and hasattr(output, "GetNodeDisplacements")


def _is_openstaadpy_session(session: StaadSession) -> bool:
    return hasattr(session.root, "AnalyzeEx") and hasattr(session.root, "Geometry") and hasattr(session.root, "Output")


def _target_has_any_method(target: Any, method_names: Iterable[str]) -> bool:
    for method_name in method_names:
        if _get_com_member(target, method_name) is not None:
            return True
    return False


def _session_is_attach_ready(session: StaadSession | None) -> bool:
    if session is None:
        return False
    if _is_openstaadpy_session(session):
        return session.geometry is not None and session.output is not None
    return _target_has_any_method(session.geometry, GEOMETRY_METHOD_NAMES) and _target_has_any_method(
        session.output,
        OUTPUT_METHOD_NAMES,
    )


def _safe_call(fn: Any) -> Any | None:
    try:
        return fn()
    except Exception:
        return None


def _safe_geometry_list(target: Any, method_names: list[str]) -> list[int]:
    for method_name in method_names:
        method = _get_com_member(target, method_name)
        if method is None:
            continue
        try:
            values = method()
        except Exception:
            continue
        sequence = _extract_numeric_sequence(values)
        if sequence:
            return [int(value) for value in sequence]
    return []


def try_accept_postprocessing_dialog() -> bool:
    if win32com is None:
        return False

    try:
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        return False

    for title in ["STAAD.Pro 2025", "STAAD.Pro", "Bentley STAAD", "OpenSTAAD"]:
        try:
            activated = shell.AppActivate(title)
        except Exception:
            activated = False
        if not activated:
            continue
        try:
            time.sleep(0.25)
            shell.SendKeys("2")
            time.sleep(0.1)
            shell.SendKeys("~")
            return True
        except Exception:
            continue

    return False


def _flag_known_methods(target: Any, method_names: list[str]) -> None:
    if target is None:
        return
    flagger = getattr(target, "_FlagAsMethod", None)
    if flagger is None:
        return
    for method_name in method_names:
        try:
            flagger(method_name)
        except Exception:
            continue


def _get_com_member(target: Any, member_name: str) -> Any | None:
    if target is None:
        return None
    try:
        return getattr(target, member_name)
    except Exception:
        return None


def _normalize_force_unit(unit: str) -> str:
    normalized = _normalize_unit_key(unit)
    aliases = {
        "KILONEWTON": "KN",
        "NEWTON": "N",
        "MEGANEWTON": "MN",
        "DECANEWTON": "DN",
        "KILOGRAM": "KG",
        "METRICTON": "TON",
        "TONNE": "TON",
        "TON": "TON",
        "KILOPOUND": "KIP",
        "POUND": "LB",
    }
    return aliases.get(normalized, normalized)


def _normalize_length_unit(unit: str) -> str:
    normalized = _normalize_unit_key(unit)
    aliases = {
        "M": "METER",
        "METER": "METER",
        "METERS": "METER",
        "METRE": "METER",
        "METRES": "METER",
        "MM": "MM",
        "MILLIMETER": "MM",
        "MILLIMETERS": "MM",
        "MILLIMETRE": "MM",
        "MILLIMETRES": "MM",
        "CM": "CM",
        "CENTIMETER": "CM",
        "CENTIMETERS": "CM",
        "CENTIMETRE": "CM",
        "CENTIMETRES": "CM",
        "FT": "FT",
        "FEET": "FT",
        "FOOT": "FT",
        "IN": "IN",
        "INCH": "IN",
        "INCHES": "IN",
        "DM": "DM",
        "DECIMETER": "DM",
        "DECIMETERS": "DM",
        "DECIMETRE": "DM",
        "DECIMETRES": "DM",
        "KM": "KM",
        "KILOMETER": "KM",
        "KILOMETERS": "KM",
        "KILOMETRE": "KM",
        "KILOMETRES": "KM",
    }
    return aliases.get(normalized, normalized)


def _normalize_unit_key(unit: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", unit.strip().upper())


def _parse_load_case_argument(value: str | None) -> int | str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.lower() == "all":
        return "all"
    try:
        return int(cleaned)
    except ValueError as exc:
        raise StaadAutomationError(f"Invalid --loadcase value: {value!r}. Use a number or 'all'.") from exc


def _safe_repr(value: Any) -> str:
    if isinstance(value, (int, float, str, bool)):
        return repr(value)
    return f"<{type(value).__name__}>"


GEOMETRY_METHOD_NAMES = [
    "GetMemberCount",
    "GetBeamCount",
    "GetMemberList",
    "GetBeamList",
    "BeamList",
    "GetAllMembers",
    "GetAllBeams",
    "GetNodeCount",
    "GetJointCount",
    "GetNodeList",
    "NodeList",
    "GetJointList",
    "GetAllNodes",
    "GetAllJoints",
    "GetMemberIncidence",
    "GetBeamIncidence",
    "GetMemberNodes",
    "GetBeamNodes",
    "GetNodeCoordinates",
    "GetJointCoordinates",
    "GetNodeCoord",
    "GetJointCoord",
]

OUTPUT_METHOD_NAMES = [
    "SetCurrentLoadCase",
    "SetActiveLoadCase",
    "SelectLoadCase",
    "GetMemberEndForces",
    "GetBeamEndForces",
    "GetMinMaxBendingMoment",
    "GetNodeDisplacements",
    "GetJointDisplacements",
    "GetIntermediateDeflectionAtDistance",
    "AreResultsAvailable",
    "IsPostProcessingReady",
]

COMMAND_METHOD_NAMES = [
    "PerformAnalysis",
    "Analyze",
    "RunAnalysis",
    "AnalyzeModel",
    "IsAnalyzing",
    "GetIsAnalyzing",
]

VIEW_METHOD_NAMES = [
    "GoToPostProcessingMode",
    "EnterPostProcessingMode",
    "SetPostProcessingMode",
    "SetInterfaceMode",
]

ROOT_METHOD_NAMES = [
    "OpenSTAADFile",
    "OpenSTAADFile2",
    "OpenFile",
    *GEOMETRY_METHOD_NAMES,
    *OUTPUT_METHOD_NAMES,
    *COMMAND_METHOD_NAMES,
    *VIEW_METHOD_NAMES,
]
