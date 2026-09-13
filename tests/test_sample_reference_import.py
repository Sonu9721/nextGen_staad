import json
from pathlib import Path
import pytest
from scripts.normalize_reference import normalize_unquoted_export

ROOT = Path(__file__).parents[1]


def test_sample6_normalization_is_reproducible_and_values_preserved():
    folder = ROOT / "examples/sample6"
    raw = (folder / "output.txt").read_text(encoding="utf-8")
    normalized = normalize_unquoted_export(raw)
    assert normalized == json.loads(
        (folder / "reference.json").read_text(encoding="utf-8")
    )
    assert (
        normalized["result"]["properties"]["bending_moment"]["major"]["value"]
        == 3.197482
    )
    assert (
        normalized["result"]["casement"]["profiles"]["central_meeting"]["member_ids"]
        == []
    )
    assert normalized["result"]["analysis"]["load_cases"] == [1, 2, 3, 4]
    assert normalized["working_directory"] in raw  # Damaged path is not invented.


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        "{\na 1,\na 2\n}",
        "{\na [\n1\n}",
        "{\na 1",
        "{\na x, y\n}",
        "{\na 1\n}\n{\nb 2\n}",
    ],
)
def test_normalization_rejects_ambiguous_or_malformed_text(text):
    with pytest.raises(ValueError):
        normalize_unquoted_export(text)


def test_normalizer_preserves_spaces_boolean_and_null():
    assert normalize_unquoted_export(
        "{\ntitle DL + WL,\nflag true,\nempty null\n}"
    ) == {"title": "DL + WL", "flag": True, "empty": None}
