"""Explicit opt-in: never launch STAAD during routine tests."""

import os
from pathlib import Path
import pytest


@pytest.mark.skipif(
    os.environ.get("RUN_OPENSTAAD_ORACLE") != "1",
    reason="Requires explicitly enabled licensed Windows OpenSTAAD oracle",
)
def test_live_openstaad_comparison():
    from engine.adapter import extractor_module, make_config, analyze_file
    from engine.comparison import compare

    path = Path(os.environ["OPENSTAAD_ORACLE_MODEL"])
    flow = os.environ.get("OPENSTAAD_ORACLE_FLOW", "standard")
    ref = extractor_module().run(make_config(path, flow))
    report = compare(ref, analyze_file(path, flow=flow))
    assert report["all_values_within_tolerance"], report
