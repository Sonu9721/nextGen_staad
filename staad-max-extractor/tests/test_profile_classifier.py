import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import extractor
import profile_classifier as pc


def _write_std(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


class ExpandStaadMemberRangesTests(unittest.TestCase):
    def test_expands_to_keyword(self) -> None:
        self.assertEqual(pc.expand_staad_member_ranges(["1", "3", "5", "TO", "8"]), [1, 3, 5, 6, 7, 8])

    def test_single_ids(self) -> None:
        self.assertEqual(pc.expand_staad_member_ranges(["10", "12"]), [10, 12])


class ParseMemberPropertyLinesTests(unittest.TestCase):
    def test_assigns_groups_in_emission_order(self) -> None:
        std_path = Path("unitized_single_panel.std")
        std_text = """STAAD SPACE
UNIT KN METER
JOINT COORDINATES
1 0 0 0
2 0 3 0
3 0 6 0
4 2 0 0
5 2 3 0
6 2 6 0
MEMBER INCIDENCES
1 1 2
2 2 3
3 4 5
4 5 6
5 1 4
6 2 5
7 3 6
MEMBER PROPERTY AMERICAN
1 2 PRIS AX 10 IX 20 IY 20 IZ 100 YD 5 ZD 8
5 PRIS AX 30 IX 40 IY 40 IZ 200 YD 6 ZD 9
6 PRIS AX 50 IX 60 IY 60 IZ 300 YD 7 ZD 10
7 PRIS AX 70 IX 80 IY 80 IZ 400 YD 8 ZD 11
3 4 PRIS AX 90 IX 100 IY 100 IZ 500 YD 9 ZD 12
PERFORM ANALYSIS
FINISH
"""
        try:
            _write_std(std_path, std_text)
            parsed = pc.parse_member_property_lines(std_path)
        finally:
            std_path.unlink(missing_ok=True)

        self.assertEqual(parsed.member_groups[1], pc.PropertyGroup.MULLION)
        self.assertEqual(parsed.member_groups[5], pc.PropertyGroup.STACK)
        self.assertEqual(parsed.member_groups[6], pc.PropertyGroup.HEAD)
        self.assertEqual(parsed.member_groups[7], pc.PropertyGroup.SILL)
        self.assertEqual(parsed.member_groups[3], pc.PropertyGroup.TRANSOM)
        self.assertEqual(parsed.member_groups[4], pc.PropertyGroup.TRANSOM)


class ClassifyProfileMembersTests(unittest.TestCase):
    def _classify(self, std_text: str, generation_request: dict | None = None) -> dict[str, list[int]]:
        std_path = Path("classify_members.std")
        try:
            _write_std(std_path, "STAAD SPACE\nUNIT KN METER\n" + std_text + "\nPERFORM ANALYSIS\nFINISH\n")
            metadata = extractor.parse_std_file_metadata(std_path)
            parsed = pc.parse_member_property_lines(std_path)
            return pc.classify_profile_members(
                parsed_properties=parsed,
                member_ids=metadata.topology.member_ids,
                member_incidences=metadata.topology.member_incidences,
                node_coordinates=metadata.topology.node_coordinates,
                generation_request=generation_request,
            )
        finally:
            std_path.unlink(missing_ok=True)

    def test_single_panel_has_no_stack_group(self) -> None:
        std_text = """JOINT COORDINATES
1 0 0 0
2 0 3 0
3 0 6 0
4 2 0 0
5 2 3 0
6 2 6 0
MEMBER INCIDENCES
1 1 2
2 2 3
3 4 5
4 5 6
5 1 4
6 2 5
7 3 6
MEMBER PROPERTY AMERICAN
1 2 PRIS AX 10 IX 20 IY 20 IZ 100 YD 5 ZD 8
6 PRIS AX 50 IX 60 IY 60 IZ 300 YD 7 ZD 10
7 PRIS AX 70 IX 80 IY 80 IZ 400 YD 8 ZD 11
5 PRIS AX 80 IX 90 IY 90 IZ 450 YD 8.5 ZD 11.5
3 4 6 PRIS AX 90 IX 100 IY 100 IZ 500 YD 9 ZD 12
"""
        groups = self._classify(std_text)
        self.assertIn("mullion", groups)
        self.assertIn("head", groups)
        self.assertIn("sill", groups)
        self.assertIn("transom", groups)
        self.assertNotIn("stack", groups)

    def test_multi_panel_separates_stack_from_transom(self) -> None:
        std_text = """JOINT COORDINATES
1 0 0 0
2 0 3 0
3 0 6 0
4 2 0 0
5 2 3 0
6 2 6 0
7 4 3 0
8 4 6 0
MEMBER INCIDENCES
1 1 2
2 2 3
3 4 5
4 5 6
5 1 4
6 2 5
7 3 6
8 5 7
9 7 8
MEMBER PROPERTY AMERICAN
1 2 7 9 PRIS AX 10 IX 20 IY 20 IZ 100 YD 5 ZD 8
8 PRIS AX 30 IX 40 IY 40 IZ 200 YD 6 ZD 9
4 PRIS AX 50 IX 60 IY 60 IZ 300 YD 7 ZD 10
5 PRIS AX 70 IX 80 IY 80 IZ 400 YD 8 ZD 11
6 PRIS AX 90 IX 100 IY 100 IZ 500 YD 9 ZD 12
"""
        groups = self._classify(
            std_text,
            generation_request={"panels": [{"segments": [3.0, 3.0]}, {"segments": [3.0]}]},
        )
        self.assertEqual(groups["stack"], [8])
        self.assertEqual(groups["transom"], [6])
        self.assertNotEqual(groups["stack"], groups["transom"])

    def test_head_and_transom_pris_collision_uses_geometry(self) -> None:
        std_text = """JOINT COORDINATES
1 0 0 0
2 0 3 0
3 0 6 0
4 2 0 0
5 2 3 0
6 2 6 0
7 4 6 0
8 4 3 0
MEMBER INCIDENCES
1 1 2
2 2 3
3 5 8
4 6 7
5 1 4
6 2 5
7 3 6
MEMBER PROPERTY AMERICAN
1 2 7 PRIS AX 10 IX 20 IY 20 IZ 100 YD 5 ZD 8
4 PRIS AX 50 IX 60 IY 60 IZ 300 YD 7 ZD 10
5 PRIS AX 70 IX 80 IY 80 IZ 400 YD 8 ZD 11
3 6 PRIS AX 50 IX 60 IY 60 IZ 300 YD 7 ZD 10
"""
        groups = self._classify(std_text)
        self.assertIn(4, groups["head"])
        self.assertIn(3, groups["transom"])
        self.assertIn(6, groups["transom"])

    def test_vertical_stack_member_stays_mullion(self) -> None:
        std_text = """JOINT COORDINATES
1 0 0 0
2 0 0.5 0
3 2 0 0
4 2 0.5 0
MEMBER INCIDENCES
1 1 2
2 3 4
MEMBER PROPERTY AMERICAN
1 2 PRIS AX 10 IX 20 IY 20 IZ 100 YD 5 ZD 8
3 4 PRIS AX 30 IX 40 IY 40 IZ 200 YD 6 ZD 9
"""
        groups = self._classify(std_text)
        self.assertEqual(groups["mullion"], [1, 2])


class ResolveExtractionFlowTests(unittest.TestCase):
    def test_defaults_to_standard(self) -> None:
        self.assertEqual(pc.resolve_extraction_flow(None, None), "standard")

    def test_form_value_wins_for_unitized(self) -> None:
        self.assertEqual(pc.resolve_extraction_flow("fully_unitized", None), "fully_unitized")

    def test_generation_request_can_enable_unitized(self) -> None:
        self.assertEqual(
            pc.resolve_extraction_flow("standard", {"flowType": "FULLY_UNITIZED"}),
            "fully_unitized",
        )

    def test_form_value_casement(self) -> None:
        self.assertEqual(pc.resolve_extraction_flow("casement", None), "casement")
        self.assertEqual(pc.normalize_extraction_flow("CASEMENT"), "casement")
        self.assertEqual(pc.normalize_extraction_flow("casement"), "casement")

    def test_generation_request_can_enable_casement(self) -> None:
        self.assertEqual(
            pc.resolve_extraction_flow("standard", {"extraction_flow": "casement"}),
            "casement",
        )

    def test_unknown_flow_stays_standard(self) -> None:
        self.assertEqual(pc.normalize_extraction_flow("sliding"), "standard")
        self.assertEqual(pc.resolve_extraction_flow("sliding", None), "standard")
