"""Tests for Casement signed envelope helpers and payload assembly."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.append(str(Path(__file__).resolve().parents[1]))

import casement_extractor as ce
import extractor


class SignedMinMaxTests(unittest.TestCase):
    def test_signed_envelope_preserves_signs(self) -> None:
        samples = [{"value": v} for v in [-10, -2, 0, 4, 8]]
        max_sample, min_sample = ce.calculate_signed_min_max(samples)
        assert max_sample is not None and min_sample is not None
        self.assertEqual(max_sample["value"], 8)
        self.assertEqual(min_sample["value"], -10)

    def test_zero_is_valid_result(self) -> None:
        samples = [{"value": 0.0, "member_id": 1}]
        max_sample, min_sample = ce.calculate_signed_min_max(samples)
        assert max_sample is not None and min_sample is not None
        self.assertEqual(max_sample["value"], 0.0)
        self.assertEqual(min_sample["value"], 0.0)

    def test_empty_samples_return_none(self) -> None:
        max_sample, min_sample = ce.calculate_signed_min_max([])
        self.assertIsNone(max_sample)
        self.assertIsNone(min_sample)


class CasementPayloadBuilderTests(unittest.TestCase):
    def _base_envelope(self) -> extractor.PropertyEnvelopeResults:
        return extractor.PropertyEnvelopeResults(
            max_bm_major_mz={"value": 4.2, "axis": "MZ", "member_id": 16, "load_case": 4},
            max_bm_minor_my={"value": 0.34, "axis": "MY", "member_id": 7, "load_case": 5},
            bm_minor_my_at_max_major_bm_point=None,
            max_sf_major_fy={"value": 5.3, "axis": "FY", "member_id": 15, "load_case": 3},
            max_sf_minor_fz_mullion_only={"value": 0.43, "axis": "FZ", "member_id": 10, "load_case": 6},
            sf_minor_fz_at_max_major_sf_point=None,
            max_axial={"value": 2.1, "axis": "FX", "member_id": 11, "load_case": 6},
            max_displacement={"value": 15.1, "direction": "RESULTANT", "member_id": 16, "load_case": 6},
        )

    def test_standard_payload_has_no_casement(self) -> None:
        payload = extractor._build_grouped_result_payload(
            file_path=extractor.Path("model.std"),
            load_case_mode="all",
            selected_load_cases=[1, 2],
            moment_mode="internal-envelope",
            include_combinations=True,
            runtime_units=extractor.DetectedUnits(force_unit="KN", length_unit="METER"),
            file_units=extractor.DetectedUnits(force_unit="KN", length_unit="METER"),
            moment_output_unit="kN-m",
            displacement_output_unit="mm",
            envelope=self._base_envelope(),
            extraction_flow="standard",
        )
        self.assertNotIn("casement", payload)
        self.assertNotIn("profiles", payload)
        self.assertNotIn("global", payload["properties"])

    def test_fully_unitized_payload_has_no_casement(self) -> None:
        payload = extractor._build_grouped_result_payload(
            file_path=extractor.Path("model.std"),
            load_case_mode="all",
            selected_load_cases=[1, 2],
            moment_mode="internal-envelope",
            include_combinations=True,
            runtime_units=extractor.DetectedUnits(force_unit="KN", length_unit="METER"),
            file_units=extractor.DetectedUnits(force_unit="KN", length_unit="METER"),
            moment_output_unit="kN-m",
            displacement_output_unit="mm",
            envelope=self._base_envelope(),
            extraction_flow="fully_unitized",
            profile_envelopes={"mullion": self._base_envelope()},
            profile_member_ids={"mullion": [1, 2]},
        )
        self.assertNotIn("casement", payload)
        self.assertEqual(payload["profiles"]["extraction_flow"], "fully_unitized")
        self.assertIn("global", payload["properties"])

    def test_casement_payload_attachment_shape(self) -> None:
        payload = extractor._build_grouped_result_payload(
            file_path=extractor.Path("model.std"),
            load_case_mode="all",
            selected_load_cases=[1, 2, 3, 4],
            moment_mode="internal-envelope",
            include_combinations=True,
            runtime_units=extractor.DetectedUnits(force_unit="KN", length_unit="METER"),
            file_units=extractor.DetectedUnits(force_unit="KN", length_unit="METER"),
            moment_output_unit="kN-m",
            displacement_output_unit="mm",
            envelope=self._base_envelope(),
            extraction_flow="casement",
        )
        # Builder itself does not attach casement; run() does. Simulate attach.
        casement = ce.build_empty_casement_payload()
        casement["profiles"]["interlock"]["member_ids"] = [22, 26]
        casement["profiles"]["interlock"]["bm"] = {
            "unit": "kN-m",
            "max": {"value": 0.743, "member_id": 22, "axis": "MZ", "load_case": 3},
            "min": {"value": -0.5, "member_id": 26, "axis": "MZ", "load_case": 4},
        }
        payload["casement"] = casement

        self.assertIn("casement", payload)
        self.assertNotIn("profiles", payload)
        self.assertNotIn("global", payload["properties"])
        self.assertEqual(payload["properties"]["bending_moment"]["major"]["value"], 4.2)
        self.assertEqual(payload["casement"]["extraction_flow"], "casement")
        for name in ("interlock", "central_meeting", "fixed_mullion", "horizontal", "outer"):
            self.assertIn(name, payload["casement"]["profiles"])
            profile = payload["casement"]["profiles"][name]
            for metric in ("bm", "sf", "af", "df"):
                self.assertIn(metric, profile)
                self.assertIn("max", profile[metric])
                self.assertIn("min", profile[metric])

        bm = payload["casement"]["profiles"]["interlock"]["bm"]
        self.assertGreaterEqual(bm["max"]["value"], bm["min"]["value"])

    def test_missing_df_does_not_crash_and_stays_null(self) -> None:
        empty = ce.build_empty_casement_payload()
        self.assertIsNone(empty["profiles"]["horizontal"]["df"]["max"])
        self.assertIsNone(empty["profiles"]["horizontal"]["df"]["min"])
        # Other metrics also null in empty payload, but shape is intact.
        self.assertEqual(empty["profiles"]["horizontal"]["bm"]["unit"], "kN-m")


class CasementExtractSoftFailureTests(unittest.TestCase):
    def test_extract_with_empty_members_still_returns_structure(self) -> None:
        fake_extractor = MagicMock()
        fake_extractor.parse_member_property_lines.side_effect = Exception("no pris")
        config = MagicMock()
        config.file_path = Path("missing.std")
        config.moment_axis = "MZ"
        config.displacement_axis = "RESULTANT"
        config.moment_output_unit = "kN-m"
        config.displacement_output_unit = "mm"
        topology = MagicMock()
        topology.member_incidences = {}
        topology.node_coordinates = {}

        # classify will return empty profiles; caches will be empty → nulls
        result = ce.extract_casement_profile_envelopes(
            extractor=fake_extractor,
            session=MagicMock(),
            config=config,
            topology=topology,
            member_ids=[],
            selected_load_cases=[1],
            runtime_units=MagicMock(force_unit="KN", length_unit="METER"),
            load_case_names={},
            generation_request=None,
        )
        self.assertEqual(result["extraction_flow"], "casement")
        self.assertIsNone(result["profiles"]["outer"]["bm"]["max"])
        self.assertIsNone(result["profiles"]["outer"]["df"]["min"])


if __name__ == "__main__":
    unittest.main()
