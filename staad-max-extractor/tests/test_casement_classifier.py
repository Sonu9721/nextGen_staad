"""Tests for Casement topology classification."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import extractor
import profile_classifier as pc
from casement_classifier import CASEMENT_PROFILE_NAMES, classify_casement_members

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "Casement_CF-3T4S-3F-C_STAAD.std"


class CasementClassifierFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not FIXTURE.is_file():
            raise unittest.SkipTest(f"Missing fixture: {FIXTURE}")
        meta = extractor.parse_std_file_metadata(FIXTURE)
        parsed = pc.parse_member_property_lines(FIXTURE)
        cls.member_ids = meta.topology.member_ids
        cls.incidences = meta.topology.member_incidences
        cls.coordinates = meta.topology.node_coordinates
        cls.member_pris = parsed.member_pris
        cls.profiles = classify_casement_members(
            member_ids=cls.member_ids,
            member_incidences=cls.incidences,
            node_coordinates=cls.coordinates,
            member_pris=cls.member_pris,
        )

    def test_expected_sample_topology(self) -> None:
        self.assertEqual(self.profiles["interlock"], [22, 26])
        self.assertEqual(self.profiles["central_meeting"], [24])
        self.assertEqual(self.profiles["fixed_mullion"], [21, 23, 25])
        self.assertEqual(self.profiles["horizontal"], [47, 48, 49, 50])
        self.assertEqual(
            self.profiles["outer"],
            list(range(1, 21)) + list(range(27, 47)) + list(range(51, 71)),
        )

    def test_all_profile_keys_present(self) -> None:
        self.assertEqual(set(self.profiles.keys()), set(CASEMENT_PROFILE_NAMES))

    def test_profiles_are_disjoint(self) -> None:
        seen: set[int] = set()
        for name in CASEMENT_PROFILE_NAMES:
            for member_id in self.profiles[name]:
                self.assertNotIn(member_id, seen, f"member {member_id} overlapped in {name}")
                seen.add(member_id)

    def test_special_profiles_not_in_outer(self) -> None:
        outer = set(self.profiles["outer"])
        for name in ("interlock", "central_meeting", "fixed_mullion", "horizontal"):
            overlap = outer.intersection(self.profiles[name])
            self.assertEqual(overlap, set(), f"{name} leaked into outer: {overlap}")

    def test_request_map_override(self) -> None:
        override = {
            "casement_profiles": {
                "interlock": [22],
                "central_meeting": [24],
                "fixed_mullion": [21],
                "horizontal": [47],
                "outer": [1, 2, 999],
            }
        }
        profiles = classify_casement_members(
            member_ids=self.member_ids,
            member_incidences=self.incidences,
            node_coordinates=self.coordinates,
            generation_request=override,
        )
        self.assertEqual(profiles["interlock"], [22])
        self.assertEqual(profiles["central_meeting"], [24])
        self.assertEqual(profiles["fixed_mullion"], [21])
        self.assertEqual(profiles["horizontal"], [47])
        self.assertEqual(profiles["outer"], [1, 2])
        self.assertNotIn(999, profiles["outer"])


if __name__ == "__main__":
    unittest.main()
