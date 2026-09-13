import unittest
from unittest.mock import patch

import extractor


class ParseArgsTests(unittest.TestCase):
    def test_displacement_axis_defaults_to_resultant(self) -> None:
        args, config = extractor.parse_args(["--file", "model.std"])

        self.assertEqual(args.displacement_axis, "RESULTANT")
        self.assertEqual(config.displacement_axis, "RESULTANT")

    def test_r_alias_normalizes_to_resultant(self) -> None:
        _, config = extractor.parse_args(["--file", "model.std", "--displacement-axis", "r"])
        self.assertEqual(config.displacement_axis, "RESULTANT")


class ParseStdMetadataTests(unittest.TestCase):
    def test_parse_std_file_metadata_collects_units_load_cases_and_topology_in_one_pass(self) -> None:
        std_text = """STAAD SPACE
UNIT KN METER
JOINT COORDINATES
1 0 0 0
2 1 0 0
MEMBER INCIDENCES
10 1 2
LOAD 1 LOADTYPE DEAD  TITLE DL
LOAD COMB 2 DL+LL
PERFORM ANALYSIS
FINISH
"""
        std_path = extractor.Path("test_metadata_model.std")
        try:
            std_path.write_text(std_text, encoding="utf-8")
            metadata = extractor.parse_std_file_metadata(std_path)
        finally:
            std_path.unlink(missing_ok=True)

        self.assertEqual(metadata.file_units.force_unit, "KN")
        self.assertEqual(metadata.file_units.length_unit, "METER")
        self.assertEqual(metadata.primary_load_cases, [1])
        self.assertEqual(metadata.available_load_cases, [1, 2])
        self.assertEqual(metadata.load_case_names, {1: "DL"})
        self.assertEqual(metadata.topology.member_ids, [10])
        self.assertEqual(metadata.topology.node_ids, [1, 2])
        self.assertEqual(metadata.topology.node_coordinates, {1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0)})
        self.assertEqual(metadata.topology.member_incidences, {10: (1, 2)})


class GetMaxBendingMomentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = extractor.StaadSession(
            root=None,
            geometry=object(),
            output=object(),
            load=None,
            command=None,
            view=None,
        )
        self.source_units = extractor.DetectedUnits(force_unit="KN", length_unit="METER")

    def test_end_forces_mode_ignores_internal_envelope_results(self) -> None:
        my_index = extractor.MOMENT_INDEX["MY"]
        start_forces = [0.0] * 6
        end_forces = [0.0] * 6
        start_forces[my_index] = -2.0
        end_forces[my_index] = 3.0

        with patch.object(extractor, "get_member_ids", return_value=[101]), patch.object(
            extractor,
            "get_member_end_forces",
            side_effect=[start_forces, end_forces],
        ), patch.object(
            extractor,
            "get_member_bending_moment_extremes",
            return_value={"minimum_value": -10.0, "minimum_station": 0.4, "maximum_value": 8.0, "maximum_station": 0.6},
        ):
            result = extractor.get_max_bending_moment(
                session=self.session,
                load_case=1,
                moment_axis="MY",
                moment_mode="end-forces",
                source_units=self.source_units,
                output_unit="kN-m",
                fallback_member_ids=[101],
            )

        self.assertEqual(result["member_id"], 101)
        self.assertEqual(result["location"], "end")
        self.assertEqual(result["station"], 1.0)
        self.assertEqual(result["value"], 3.0)

    def test_internal_envelope_mode_uses_member_extremes_only(self) -> None:
        with patch.object(extractor, "get_member_ids", return_value=[202]), patch.object(
            extractor,
            "get_member_end_forces",
            side_effect=AssertionError("end forces should not be queried for internal-envelope mode"),
        ), patch.object(
            extractor,
            "get_member_bending_moment_extremes",
            return_value={"minimum_value": -7.5, "minimum_station": 0.25, "maximum_value": 5.0, "maximum_station": 0.75},
        ):
            result = extractor.get_max_bending_moment(
                session=self.session,
                load_case=2,
                moment_axis="MY",
                moment_mode="internal-envelope",
                source_units=self.source_units,
                output_unit="kN-m",
                fallback_member_ids=[202],
            )

        self.assertEqual(result["member_id"], 202)
        self.assertEqual(result["location"], "member-minimum")
        self.assertEqual(result["station"], 0.25)
        self.assertEqual(result["station_unit"], "meter")
        self.assertEqual(result["value"], 7.5)

    def test_select_governing_bending_moment_picks_largest_value(self) -> None:
        result = extractor._select_governing_bending_moment(
            {
                "MY": {"value": 0.098842, "axis": "MY", "member_id": 12},
                "MZ": {"value": 2.59287, "axis": "MZ", "member_id": 5},
            }
        )

        self.assertEqual(result["axis"], "MZ")
        self.assertEqual(result["member_id"], 5)
        self.assertEqual(result["value"], 2.59287)

    def test_minor_my_is_read_at_same_end_as_major_mz_result(self) -> None:
        point_result = {
            "value": 12.0,
            "unit": "kN-m",
            "member_id": 101,
            "location": "end",
            "load_case": 7,
            "station": 1.0,
            "station_unit": None,
            "axis": "MZ",
        }
        forces = [0.0] * 6
        forces[extractor.MOMENT_INDEX["MY"]] = -4.25

        with patch.object(extractor, "get_member_end_forces", return_value=forces) as get_end_forces:
            result = extractor.get_axis_result_at_point(
                session=self.session,
                point_result=point_result,
                axis="MY",
                source_units=self.source_units,
                output_unit="kN-m",
            )

        get_end_forces.assert_called_once_with(self.session.output, 101, 1, 7)
        self.assertEqual(result["member_id"], 101)
        self.assertEqual(result["location"], "end")
        self.assertEqual(result["load_case"], 7)
        self.assertEqual(result["station"], 1.0)
        self.assertEqual(result["axis"], "MY")
        self.assertEqual(result["value"], 4.25)

    def test_internal_envelope_minor_lookup_returns_none_when_unavailable(self) -> None:
        point_result = {
            "value": 12.0,
            "unit": "kN-m",
            "member_id": 202,
            "location": "member-minimum",
            "load_case": 8,
            "station": 0.35,
            "station_unit": "meter",
            "axis": "MZ",
        }

        with patch.object(
            extractor,
            "get_member_intermediate_forces_at_distance",
            side_effect=extractor.StaadAutomationError("intermediate forces unavailable"),
        ):
            result = extractor._safe_get_axis_result_at_point(
                session=self.session,
                point_result=point_result,
                axis="MY",
                source_units=self.source_units,
                output_unit="kN-m",
            )

        self.assertIsNone(result)


class CalculateRelativeTransomDeflectionTests(unittest.TestCase):
    def test_staad_beam_71_example(self) -> None:
        result = extractor._calculate_relative_transom_deflection(14.397, 7.869, 7.869)
        self.assertAlmostEqual(result, 6.528, places=3)

    def test_unequal_support_displacements(self) -> None:
        result = extractor._calculate_relative_transom_deflection(20.0, 8.0, 5.0)
        self.assertAlmostEqual(result, 15.0)

    def test_zero_support_displacements(self) -> None:
        result = extractor._calculate_relative_transom_deflection(12.0, 0.0, 0.0)
        self.assertAlmostEqual(result, 12.0)


class GetMaxDisplacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = extractor.StaadSession(
            root=None,
            geometry=object(),
            output=object(),
            load=None,
            command=None,
            view=None,
        )
        self.source_units = extractor.DetectedUnits(force_unit="KN", length_unit="METER")

    def test_member_span_deflection_handles_two_component_tuple_for_z_axis(self) -> None:
        with patch.object(extractor, "get_node_ids", return_value=[1]), patch.object(
            extractor,
            "get_node_displacements",
            return_value=[0.0, 0.0, 0.0],
        ), patch.object(
            extractor,
            "get_member_ids",
            return_value=[77],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=1.0,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[0.5],
        ), patch.object(
            extractor,
            "get_member_intermediate_deflection",
            return_value=(0.001, 0.25),
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=2,
                displacement_axis="Z",
                source_units=self.source_units,
                output_unit="mm",
                fallback_member_ids=[77],
                fallback_node_ids=[1],
            )

        self.assertEqual(result["source"], "member-span")
        self.assertEqual(result["member_id"], 77)
        self.assertEqual(result["station"], 0.5)
        self.assertEqual(result["station_unit"], "meter")
        self.assertEqual(result["value"], 250.0)

    def test_node_resultant_uses_xyz_magnitude(self) -> None:
        with patch.object(extractor, "get_node_ids", return_value=[1]), patch.object(
            extractor,
            "get_node_displacements",
            return_value=[3.0, 4.0, 12.0],
        ), patch.object(
            extractor,
            "get_member_ids",
            return_value=[],
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=2,
                displacement_axis="RESULTANT",
                source_units=self.source_units,
                output_unit="mm",
                fallback_member_ids=[],
                fallback_node_ids=[1],
            )

        self.assertEqual(result["source"], "node")
        self.assertEqual(result["node_id"], 1)
        self.assertEqual(result["direction"], "RESULTANT")
        self.assertEqual(result["value"], 13000.0)

    def test_members_only_skips_nodal_displacement_even_when_nodes_govern_globally(self) -> None:
        with patch.object(extractor, "get_node_displacements") as get_node_displacements, patch.object(
            extractor,
            "get_member_ids",
            return_value=[12],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=2.0,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[1.0],
        ), patch.object(
            extractor,
            "get_member_intermediate_deflection",
            return_value=(0.0, 0.002),
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=3,
                displacement_axis="RESULTANT",
                source_units=self.source_units,
                output_unit="mm",
                member_ids=[12],
                node_ids=[99],
                members_only=True,
            )

        get_node_displacements.assert_not_called()
        self.assertEqual(result["source"], "member-span")
        self.assertEqual(result["member_id"], 12)
        self.assertEqual(result["value"], 2.0)

    def test_members_only_applies_relative_transom_deflection(self) -> None:
        node_displacements = {
            23: [0.0, 0.0, -0.007869],
            36: [0.0, 0.0, -0.007869],
        }

        def fake_get_node_displacements(_output: object, node_id: int, _load_case: int) -> list[float]:
            return node_displacements[node_id]

        with patch.object(extractor, "get_node_displacements", side_effect=fake_get_node_displacements), patch.object(
            extractor,
            "get_member_ids",
            return_value=[71],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=2.235,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[1.117],
        ), patch.object(
            extractor,
            "get_member_intermediate_deflection",
            return_value=(0.0, -0.014397),
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=2,
                displacement_axis="Z",
                source_units=self.source_units,
                output_unit="mm",
                member_ids=[71],
                node_ids=[23, 36],
                members_only=True,
                member_incidences={71: (23, 36)},
            )

        self.assertEqual(result["source"], "member-span")
        self.assertEqual(result["member_id"], 71)
        self.assertAlmostEqual(result["value"], 6.528, places=3)

    def test_members_only_governs_by_global_deflection_not_relative(self) -> None:
        node_displacements = {
            23: [0.0, 0.0, -0.007869],
            36: [0.0, 0.0, -0.007869],
            1: [0.0, 0.0, 0.0],
            2: [0.0, 0.0, 0.0],
        }
        member_deflections = {
            71: (0.0, -0.014397),
            50: (0.0, -0.012),
        }

        def fake_get_node_displacements(_output: object, node_id: int, _load_case: int) -> list[float]:
            return node_displacements[node_id]

        def fake_get_member_intermediate_deflection(
            _output: object,
            member_id: int,
            _station: float,
            _load_case: int,
        ) -> tuple[float, float]:
            return member_deflections[member_id]

        with patch.object(extractor, "get_node_displacements", side_effect=fake_get_node_displacements), patch.object(
            extractor,
            "get_member_ids",
            return_value=[71, 50],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=2.235,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[1.117],
        ), patch.object(
            extractor,
            "get_member_intermediate_deflection",
            side_effect=fake_get_member_intermediate_deflection,
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=2,
                displacement_axis="Z",
                source_units=self.source_units,
                output_unit="mm",
                member_ids=[71, 50],
                members_only=True,
                member_incidences={71: (23, 36), 50: (1, 2)},
            )

        self.assertEqual(result["member_id"], 71)
        self.assertAlmostEqual(result["value"], 6.528, places=3)
        self.assertAlmostEqual(result["selection_global_value"], 14.397, places=3)

    def test_members_only_relative_transom_unequal_supports(self) -> None:
        node_displacements = {
            1: [0.0, 0.008, 0.0],
            2: [0.0, 0.005, 0.0],
        }

        def fake_get_node_displacements(_output: object, node_id: int, _load_case: int) -> list[float]:
            return node_displacements[node_id]

        with patch.object(extractor, "get_node_displacements", side_effect=fake_get_node_displacements), patch.object(
            extractor,
            "get_member_ids",
            return_value=[10],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=2.0,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[1.0],
        ), patch.object(
            extractor,
            "get_member_intermediate_deflection",
            return_value=(0.02, 0.0),
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=1,
                displacement_axis="Y",
                source_units=self.source_units,
                output_unit="mm",
                member_ids=[10],
                members_only=True,
                member_incidences={10: (1, 2)},
            )

        self.assertAlmostEqual(result["value"], 15.0)

    def test_members_only_relative_transom_zero_supports(self) -> None:
        with patch.object(
            extractor,
            "get_node_displacements",
            return_value=[0.0, 0.0, 0.0],
        ), patch.object(
            extractor,
            "get_member_ids",
            return_value=[10],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=2.0,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[1.0],
        ), patch.object(
            extractor,
            "get_member_intermediate_deflection",
            return_value=(0.0, 0.012),
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=1,
                displacement_axis="Z",
                source_units=self.source_units,
                output_unit="mm",
                member_ids=[10],
                members_only=True,
                member_incidences={10: (1, 2)},
            )

        self.assertAlmostEqual(result["value"], 12.0)

    def test_member_span_resultant_uses_absolute_trans_displacement_magnitude(self) -> None:
        with patch.object(extractor, "get_node_ids", return_value=[1]), patch.object(
            extractor,
            "get_node_displacements",
            return_value=[0.0, 0.0, 0.0],
        ), patch.object(
            extractor,
            "get_member_ids",
            return_value=[77],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=1.0,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[0.5],
        ), patch.object(
            extractor,
            "get_member_intermediate_abs_trans_displacements",
            return_value=(0.0, 0.3, 0.4),
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=2,
                displacement_axis="RESULTANT",
                source_units=self.source_units,
                output_unit="mm",
                fallback_member_ids=[77],
                fallback_node_ids=[1],
            )

        self.assertEqual(result["source"], "member-span")
        self.assertEqual(result["member_id"], 77)
        self.assertEqual(result["station"], 0.5)
        self.assertEqual(result["value"], 500.0)

    def test_member_span_resultant_falls_back_to_deflection_tuple(self) -> None:
        with patch.object(extractor, "get_node_ids", return_value=[1]), patch.object(
            extractor,
            "get_node_displacements",
            return_value=[0.0, 0.0, 0.0],
        ), patch.object(
            extractor,
            "get_member_ids",
            return_value=[77],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=1.0,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[0.5],
        ), patch.object(
            extractor,
            "get_member_intermediate_abs_trans_displacements",
            side_effect=extractor.StaadAutomationError("absolute displacement not available"),
        ), patch.object(
            extractor,
            "get_member_intermediate_deflection",
            return_value=(0.3, 0.4),
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=2,
                displacement_axis="RESULTANT",
                source_units=self.source_units,
                output_unit="mm",
                fallback_member_ids=[77],
                fallback_node_ids=[1],
            )

        self.assertEqual(result["source"], "member-span")
        self.assertEqual(result["member_id"], 77)
        self.assertEqual(result["station"], 0.5)
        self.assertEqual(result["value"], 500.0)

    def test_member_deflection_errors_are_skipped_and_node_result_is_retained(self) -> None:
        with patch.object(extractor, "get_node_ids", return_value=[1]), patch.object(
            extractor,
            "get_node_displacements",
            return_value=[0.0, 0.0, 0.1],
        ), patch.object(
            extractor,
            "get_member_ids",
            return_value=[77],
        ), patch.object(
            extractor,
            "_get_member_length",
            return_value=1.0,
        ), patch.object(
            extractor,
            "_sample_stations",
            return_value=[0.25, 0.75],
        ), patch.object(
            extractor,
            "get_member_intermediate_deflection",
            side_effect=extractor.StaadAutomationError("[-1] General error."),
        ):
            result = extractor.get_max_displacement(
                session=self.session,
                load_case=2,
                displacement_axis="Z",
                source_units=self.source_units,
                output_unit="mm",
                fallback_member_ids=[77],
                fallback_node_ids=[1],
            )

        self.assertEqual(result["source"], "node")
        self.assertEqual(result["node_id"], 1)
        self.assertEqual(result["value"], 100.0)


class GetMemberForceResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = extractor.StaadSession(
            root=None,
            geometry=object(),
            output=object(),
            load=None,
            command=None,
            view=None,
        )
        self.source_units = extractor.DetectedUnits(force_unit="KN", length_unit="METER")

    def test_get_max_axial_force_uses_member_end_fx(self) -> None:
        start_forces = [0.0] * 6
        end_forces = [0.0] * 6
        start_forces[extractor.FORCE_INDEX["FX"]] = -7.0
        end_forces[extractor.FORCE_INDEX["FX"]] = 9.5

        with patch.object(extractor, "get_member_ids", return_value=[301]), patch.object(
            extractor,
            "get_member_end_forces",
            side_effect=[start_forces, end_forces],
        ):
            result = extractor.get_max_axial_force(
                session=self.session,
                load_case=4,
                source_units=self.source_units,
                output_unit="kN",
                fallback_member_ids=[301],
            )

        self.assertEqual(result["member_id"], 301)
        self.assertEqual(result["location"], "end")
        self.assertEqual(result["axis"], "FX")
        self.assertEqual(result["value"], 9.5)

    def test_get_max_shear_force_picks_largest_fy_or_fz(self) -> None:
        start_forces = [0.0] * 6
        end_forces = [0.0] * 6
        start_forces[extractor.FORCE_INDEX["FY"]] = -4.0
        start_forces[extractor.FORCE_INDEX["FZ"]] = 3.0
        end_forces[extractor.FORCE_INDEX["FY"]] = 6.5
        end_forces[extractor.FORCE_INDEX["FZ"]] = -8.25

        with patch.object(extractor, "get_member_ids", return_value=[401]), patch.object(
            extractor,
            "get_member_end_forces",
            side_effect=[start_forces, end_forces],
        ):
            result = extractor.get_max_shear_force(
                session=self.session,
                load_case=5,
                source_units=self.source_units,
                output_unit="kN",
                fallback_member_ids=[401],
            )

        self.assertEqual(result["member_id"], 401)
        self.assertEqual(result["location"], "end")
        self.assertEqual(result["axis"], "FZ")
        self.assertEqual(result["value"], 8.25)

    def test_major_fy_and_minor_fz_are_read_from_same_shear_point(self) -> None:
        start_forces = [0.0] * 6
        end_forces = [0.0] * 6
        start_forces[extractor.FORCE_INDEX["FY"]] = -6.0
        start_forces[extractor.FORCE_INDEX["FZ"]] = 100.0
        end_forces[extractor.FORCE_INDEX["FY"]] = 5.0
        end_forces[extractor.FORCE_INDEX["FZ"]] = 2.0

        def fake_end_forces(output, member_id, end, load_case):
            return start_forces if end == 0 else end_forces

        with patch.object(extractor, "get_member_ids", return_value=[401]), patch.object(
            extractor,
            "get_member_end_forces",
            side_effect=fake_end_forces,
        ):
            major_fy = extractor.get_max_shear_force_axis(
                session=self.session,
                load_case=5,
                shear_axis="FY",
                source_units=self.source_units,
                output_unit="kN",
                fallback_member_ids=[401],
            )
            minor_fz = extractor.get_axis_result_at_point(
                session=self.session,
                point_result=major_fy,
                axis="FZ",
                source_units=self.source_units,
                output_unit="kN",
            )

        self.assertEqual(major_fy["member_id"], 401)
        self.assertEqual(major_fy["location"], "start")
        self.assertEqual(major_fy["axis"], "FY")
        self.assertEqual(major_fy["value"], 6.0)
        self.assertEqual(minor_fz["member_id"], 401)
        self.assertEqual(minor_fz["location"], "start")
        self.assertEqual(minor_fz["axis"], "FZ")
        self.assertEqual(minor_fz["value"], 100.0)

    def test_minor_fz_at_major_shear_point_envelopes_fixed_point_across_load_cases(self) -> None:
        major_fy = {
            "value": 6.0,
            "unit": "kN",
            "member_id": 401,
            "location": "start",
            "station": 0.0,
            "station_unit": None,
            "end_index": 0,
            "load_case": 5,
            "axis": "FY",
        }
        forces_by_load_case = {
            5: [0.0, 6.0, 4.0, 0.0, 0.0, 0.0],
            6: [0.0, 2.0, -9.0, 0.0, 0.0, 0.0],
        }

        def fake_end_forces(output, member_id, end, load_case):
            self.assertEqual(member_id, 401)
            self.assertEqual(end, 0)
            return forces_by_load_case[load_case]

        with patch.object(extractor, "get_member_end_forces", side_effect=fake_end_forces):
            result = extractor._safe_get_axis_result_at_fixed_point_envelope(
                session=self.session,
                point_result=major_fy,
                axis="FZ",
                load_cases=[5, 6],
                source_units=self.source_units,
                output_unit="kN",
                load_case_names={6: "WIND"},
            )

        self.assertEqual(result["value"], 9.0)
        self.assertEqual(result["axis"], "FZ")
        self.assertEqual(result["member_id"], 401)
        self.assertEqual(result["governing_load_case_for_fz"], 6)
        self.assertEqual(result["governing_load_case_name_for_fz"], "WIND")
        self.assertEqual(result["reference_major_sf_load_case"], 5)
        self.assertEqual(result["reference_major_sf_value"], 6.0)

    def test_grouped_payload_omits_legacy_scattered_result_keys(self) -> None:
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
            max_bm_major_mz={
                "value": 4.2,
                "unit": "kN-m",
                "axis": "MZ",
                "member_id": 16,
                "location": "member-maximum",
                "station": 73.49,
                "station_unit": "in",
                "load_case": 4,
            },
            max_bm_minor_my={
                "value": 0.34,
                "unit": "kN-m",
                "axis": "MY",
                "member_id": 7,
                "location": "member-minimum",
                "station": 45.93,
                "station_unit": "in",
                "load_case": 5,
            },
            bm_minor_my_at_max_major_bm_point={
                "value": 0.1,
                "unit": "kN-m",
                "axis": "MY",
                "member_id": 16,
                "location": "member-maximum",
                "station": 73.49,
                "station_unit": "in",
                "load_case": 4,
                "signed_value": -0.1,
            },
            max_sf_major_fy={
                "value": 5.3,
                "unit": "kN",
                "axis": "FY",
                "member_id": 15,
                "member_type": "mullion",
                "location": "start",
                "station": 0.0,
                "station_unit": None,
                "load_case": 3,
                "governing_end": "start",
                "signed_value": -5.3,
                "original_value": -5.3,
            },
            max_sf_minor_fz_mullion_only={
                "value": 0.43,
                "unit": "kN",
                "axis": "FZ",
                "member_id": 10,
                "member_type": "mullion",
                "location": "start",
                "station": 0.0,
                "load_case": 6,
                "governing_end": "start",
            },
            sf_minor_fz_at_max_major_sf_point={
                "value": 0.35,
                "unit": "kN",
                "axis": "FZ",
                "member_id": 15,
                "location": "start",
                "station": 0.0,
                "station_unit": None,
                "load_case": 5,
                "governing_load_case_for_fz": 5,
                "reference_major_sf_load_case": 3,
                "reference_major_sf_value": 5.3,
                "signed_value": 0.35,
                "note": "internal",
            },
            max_axial={
                "value": 2.1,
                "unit": "kN",
                "axis": "FX",
                "member_id": 11,
                "location": "end",
                "load_case": 6,
            },
            max_displacement={
                "value": 15.1,
                "unit": "mm",
                "direction": "RESULTANT",
                "member_id": 16,
                "source": "member-span",
                "station": 73.49,
                "station_unit": "in",
                "load_case": 6,
            },
        )

        self.assertEqual(payload["properties"]["bending_moment"]["major"]["value"], 4.2)
        self.assertEqual(payload["properties"]["bending_moment"]["minor"]["max"]["value"], 0.34)
        self.assertEqual(payload["properties"]["shear_force"]["major"]["value"], 5.3)
        self.assertEqual(payload["properties"]["shear_force"]["minor"]["max_on_mullions"]["value"], 0.43)
        self.assertEqual(
            payload["properties"]["shear_force"]["minor"]["at_major_governing_point"]["governing_load_case"],
            5,
        )
        self.assertNotIn("max_bending_moment", payload)
        self.assertNotIn("max_shear_force", payload)
        self.assertNotIn("unit", payload["properties"]["shear_force"]["major"])
        self.assertNotIn("signed_value", payload["properties"]["shear_force"]["major"])
        self.assertNotIn("station_unit", payload["properties"]["shear_force"]["major"])
        self.assertNotIn("global", payload["properties"])
        self.assertNotIn("mullion", payload["properties"])
        self.assertNotIn("profiles", payload)
        self.assertNotIn("casement", payload)

    def test_unitized_payload_includes_profile_blocks(self) -> None:
        envelope = extractor.PropertyEnvelopeResults(
            max_bm_major_mz={"value": 4.2, "axis": "MZ", "member_id": 16, "load_case": 4},
            max_bm_minor_my={"value": 0.34, "axis": "MY", "member_id": 7, "load_case": 5},
            bm_minor_my_at_max_major_bm_point={"value": 0.1, "axis": "MY", "member_id": 16, "load_case": 4},
            max_sf_major_fy={"value": 5.3, "axis": "FY", "member_id": 15, "load_case": 3},
            max_sf_minor_fz_mullion_only={"value": 0.43, "axis": "FZ", "member_id": 10, "load_case": 6},
            sf_minor_fz_at_max_major_sf_point={"value": 0.35, "axis": "FZ", "member_id": 15, "load_case": 5},
            max_axial={"value": 2.1, "axis": "FX", "member_id": 11, "load_case": 6},
            max_displacement={"value": 15.1, "direction": "RESULTANT", "member_id": 16, "load_case": 6},
        )
        mullion_envelope = extractor.PropertyEnvelopeResults(
            max_bm_major_mz={"value": 3.0, "axis": "MZ", "member_id": 1, "load_case": 4},
            max_bm_minor_my={"value": 0.2, "axis": "MY", "member_id": 1, "load_case": 5},
            bm_minor_my_at_max_major_bm_point=None,
            max_sf_major_fy={"value": 4.0, "axis": "FY", "member_id": 1, "load_case": 3},
            max_sf_minor_fz_mullion_only={"value": 0.3, "axis": "FZ", "member_id": 1, "load_case": 6},
            sf_minor_fz_at_max_major_sf_point=None,
            max_axial={"value": 1.5, "axis": "FX", "member_id": 1, "load_case": 6},
            max_displacement={"value": 10.0, "direction": "RESULTANT", "member_id": 1, "load_case": 6},
        )
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
            envelope=envelope,
            extraction_flow="fully_unitized",
            profile_envelopes={"mullion": mullion_envelope},
            profile_member_ids={"mullion": [1, 2]},
        )

        self.assertEqual(payload["properties"]["bending_moment"]["major"]["value"], 4.2)
        self.assertEqual(payload["properties"]["global"]["bending_moment"]["major"]["value"], 4.2)
        self.assertEqual(payload["properties"]["mullion"]["bending_moment"]["major"]["value"], 3.0)
        self.assertIn("minor", payload["properties"]["mullion"]["bending_moment"])
        self.assertNotIn("minor", payload["properties"]["head"] if "head" in payload["properties"] else {})
        self.assertEqual(payload["profiles"]["extraction_flow"], "fully_unitized")
        self.assertEqual(payload["profiles"]["member_counts"]["mullion"], 2)
        self.assertNotIn("casement", payload)

    def test_max_minor_fz_can_be_restricted_to_vertical_members(self) -> None:
        topology = extractor.ParsedModelTopology(
            member_ids=[1, 2],
            node_ids=[1, 2, 3, 4],
            node_coordinates={
                1: (0.0, 0.0, 0.0),
                2: (0.0, 4.0, 0.0),
                3: (0.0, 4.0, 0.0),
                4: (3.0, 4.0, 0.0),
            },
            member_incidences={1: (1, 2), 2: (3, 4)},
        )
        vertical_member_ids = extractor._get_vertical_member_ids(self.session.geometry, [1, 2], topology)
        member_forces = {
            1: [0.0, 0.0, 0.43, 0.0, 0.0, 0.0],
            2: [0.0, 0.0, 0.953, 0.0, 0.0, 0.0],
        }

        def fake_end_forces(output, member_id, end, load_case):
            return member_forces[member_id]

        with patch.object(extractor, "get_member_end_forces", side_effect=fake_end_forces):
            result = extractor.get_shear_force_axis_envelope(
                session=self.session,
                load_cases=[1],
                shear_axis="FZ",
                source_units=self.source_units,
                output_unit="kN",
                member_ids=vertical_member_ids,
            )

        self.assertEqual(vertical_member_ids, [1])
        self.assertEqual(result["member_id"], 1)
        self.assertEqual(result["axis"], "FZ")
        self.assertEqual(result["value"], 0.43)

    def test_get_inner_vertical_member_ids_excludes_peripheral_columns(self) -> None:
        topology = extractor.ParsedModelTopology(
            member_ids=[1, 2, 3],
            node_ids=[1, 2, 3, 4, 5, 6],
            node_coordinates={
                1: (0.0, 0.0, 0.0),
                2: (0.0, 3.0, 0.0),
                3: (2.0, 0.0, 0.0),
                4: (2.0, 3.0, 0.0),
                5: (4.0, 0.0, 0.0),
                6: (4.0, 3.0, 0.0),
            },
            member_incidences={1: (1, 2), 2: (3, 4), 3: (5, 6)},
        )

        inner_vertical_member_ids = extractor._get_inner_vertical_member_ids(
            self.session.geometry,
            [1, 2, 3],
            topology,
        )

        self.assertEqual(inner_vertical_member_ids, [2])

    def test_get_inner_vertical_member_ids_returns_empty_for_single_bay(self) -> None:
        topology = extractor.ParsedModelTopology(
            member_ids=[1, 2],
            node_ids=[1, 2, 3, 4],
            node_coordinates={
                1: (0.0, 0.0, 0.0),
                2: (0.0, 3.0, 0.0),
                3: (4.0, 0.0, 0.0),
                4: (4.0, 3.0, 0.0),
            },
            member_incidences={1: (1, 2), 2: (3, 4)},
        )

        inner_vertical_member_ids = extractor._get_inner_vertical_member_ids(
            self.session.geometry,
            [1, 2],
            topology,
        )

        self.assertEqual(inner_vertical_member_ids, [])

    def test_get_max_axial_force_uses_inner_vertical_members_only(self) -> None:
        topology = extractor.ParsedModelTopology(
            member_ids=[1, 2, 3],
            node_ids=[1, 2, 3, 4, 5, 6],
            node_coordinates={
                1: (0.0, 0.0, 0.0),
                2: (0.0, 3.0, 0.0),
                3: (2.0, 0.0, 0.0),
                4: (2.0, 3.0, 0.0),
                5: (4.0, 0.0, 0.0),
                6: (4.0, 3.0, 0.0),
            },
            member_incidences={1: (1, 2), 2: (3, 4), 3: (5, 6)},
        )
        member_forces = {
            1: [12.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            2: [7.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            3: [15.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        }

        def fake_end_forces(output, member_id, end, load_case):
            return member_forces[member_id]

        with patch.object(extractor, "get_member_end_forces", side_effect=fake_end_forces):
            result = extractor._extract_max_axial_result(
                session=self.session,
                load_case_mode="single",
                selected_load_cases=[1],
                runtime_units=self.source_units,
                topology=topology,
                member_ids=[1, 2, 3],
                load_case_names={1: "LC1"},
            )

        self.assertEqual(result["member_id"], 2)
        self.assertEqual(result["value"], 7.0)
        self.assertEqual(result["member_type"], "inner_mullion")

    def test_axial_and_shear_envelopes_pick_largest_load_case(self) -> None:
        with patch.object(
            extractor,
            "get_max_axial_force",
            side_effect=[
                {"value": 11.0, "load_case": 1, "member_id": 10},
                {"value": 13.5, "load_case": 2, "member_id": 20},
            ],
        ), patch.object(
            extractor,
            "get_max_shear_force",
            side_effect=[
                {"value": 4.0, "load_case": 1, "member_id": 30},
                {"value": 9.0, "load_case": 2, "member_id": 40},
            ],
        ):
            axial = extractor.get_axial_force_envelope(
                session=self.session,
                load_cases=[1, 2],
                source_units=self.source_units,
                output_unit="kN",
                fallback_member_ids=[10, 20],
            )
            shear = extractor.get_shear_force_envelope(
                session=self.session,
                load_cases=[1, 2],
                source_units=self.source_units,
                output_unit="kN",
                fallback_member_ids=[30, 40],
            )

        self.assertEqual(axial["load_case"], 2)
        self.assertEqual(axial["value"], 13.5)
        self.assertEqual(shear["load_case"], 2)
        self.assertEqual(shear["value"], 9.0)


class SampleStationsTests(unittest.TestCase):
    def test_sample_stations_returns_interior_points(self) -> None:
        stations = extractor._sample_stations(10.0, 5)
        self.assertEqual(stations, [1.666667, 3.333333, 5.0, 6.666667, 8.333333])


class ConnectToStaadTests(unittest.TestCase):
    def test_connect_to_staad_uses_com_wait_after_launch_when_openstaadpy_attach_fails(self) -> None:
        com_session = extractor.StaadSession(
            root=object(),
            geometry=object(),
            output=object(),
            load=None,
            command=None,
            view=None,
        )

        with patch.object(extractor, "_connect_via_openstaadpy", return_value=None), patch.object(
            extractor,
            "launch_staad_application",
        ) as launch_mock, patch.object(
            extractor,
            "wait_for_openstaadpy_connection",
            return_value=None,
        ), patch.object(
            extractor,
            "wait_for_com_connection",
            return_value=com_session,
        ), patch.object(
            extractor,
            "_session_is_attach_ready",
            side_effect=lambda session: session is com_session,
        ):
            session = extractor.connect_to_staad(extractor.Path(r"C:\jobs\model.std"))

        launch_mock.assert_called_once()
        self.assertIs(session, com_session)

    def test_connect_to_staad_raises_clear_error_when_no_usable_interfaces_appear(self) -> None:
        with patch.object(extractor, "_connect_via_openstaadpy", return_value=None), patch.object(
            extractor,
            "launch_staad_application",
        ), patch.object(
            extractor,
            "wait_for_openstaadpy_connection",
            return_value=None,
        ), patch.object(
            extractor,
            "wait_for_com_connection",
            return_value=None,
        ), patch.object(
            extractor,
            "win32com",
            object(),
        ):
            with self.assertRaises(extractor.StaadAutomationError) as exc_info:
                extractor.connect_to_staad(extractor.Path(r"C:\jobs\model.std"))

        self.assertIn("interactive desktop session", str(exc_info.exception))


class SessionAttachReadinessTests(unittest.TestCase):
    def test_partial_openstaadpy_session_is_not_attach_ready(self) -> None:
        class PartialRoot:
            Geometry = None
            Output = None

            def AnalyzeEx(self, *_args):
                return 0

        session = extractor.StaadSession(
            root=PartialRoot(),
            geometry=None,
            output=None,
            load=None,
            command=None,
            view=None,
        )

        self.assertFalse(extractor._session_is_attach_ready(session))


if __name__ == "__main__":
    unittest.main()
