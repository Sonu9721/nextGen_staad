"""Single conversion boundary. Factors match the existing extractor."""

from dataclasses import dataclass
from .errors import AnalysisError

FORCE_N = {
    "N": 1.0,
    "KN": 1000.0,
    "MN": 1e6,
    "DN": 10.0,
    "KIP": 4448.2216152605,
    "LB": 4.4482216152605,
    "KG": 9.80665,
    "KGF": 9.80665,
    "TON": 9806.65,
    "TONNE": 9806.65,
    "T": 9806.65,
}
LENGTH_M = {
    "METER": 1.0,
    "MM": 0.001,
    "CM": 0.01,
    "IN": 0.0254,
    "FT": 0.3048,
    "DM": 0.1,
    "KM": 1000.0,
}
ALIASES = {
    "M": "METER",
    "MET": "METER",
    "METERS": "METER",
    "METRE": "METER",
    "MMS": "MM",
    "MILLIMETER": "MM",
    "MILLIMETRE": "MM",
    "CMS": "CM",
    "CENTIMETER": "CM",
    "CENTIMETRE": "CM",
    "INCH": "IN",
    "INCHES": "IN",
    "FOOT": "FT",
    "FEET": "FT",
    "NEWTON": "N",
    "NEWTONS": "N",
    "KILONEWTON": "KN",
    "KIPS": "KIP",
    "POUND": "LB",
    "POUNDS": "LB",
}


@dataclass(frozen=True)
class Units:
    length: str = "METER"
    force: str = "KN"

    @property
    def l(self):
        return LENGTH_M[self.length]

    @property
    def f(self):
        return FORCE_N[self.force]

    @classmethod
    def parse(cls, tokens):
        tokens = [ALIASES.get(t.upper(), t.upper()) for t in tokens]
        length = [t for t in tokens if t in LENGTH_M]
        force = [t for t in tokens if t in FORCE_N]
        if len(tokens) != 2 or len(length) != 1 or len(force) != 1:
            raise AnalysisError(
                "INVALID_STD_FILE", f"Unsupported UNIT {' '.join(tokens)}"
            )
        return cls(length[0], force[0])
