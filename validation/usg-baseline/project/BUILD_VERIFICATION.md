# Build verification — v0.3.0

Retested on Windows with Python 3.14, 13 September 2026.

* Full automated suite: **234 passed, 1 licensed-oracle test skipped, 0 failed**, in 143.70 seconds. Eight dependency/deprecation warnings remain. Evidence: `validation/retest.xml`.
* Real HTTP server: **14 workflow groups passed**. Six models completed analysis; Sample 7 correctly failed as a loaded mechanism. The checks cover upload, progress, geometry, export, repeatability, profile JSON, malformed input, upload limits, queued/running cancellation and retained geometry after rejection. Evidence: `validation/e2e.json`.
* New mechanics: 43 concentrated-moment tests cover closed forms, equivalent split-member nodal moments, releases, transformations, torsion, combinations, units, both-sided extrema, member endpoints and load scaling. Eight normalization checks preserve the damaged reference's values and reject ambiguous input. Sample 6 joins the reference contract suite; Sample 7 has a direct rigid-mode proof and a real API rejection test.
* Browser: Sample 7 displayed its affected-node diagnostic and 48-node/68-member geometry without fabricated results. Sample 6 then completed from the example menu, showing 44 nodes, 61 members and four cases. Physical/legacy switching, member labels, 3D/front controls and JSON export worked. The downloaded JSON exactly matches the source calculation for properties, casement profiles and physical response, and identifies solver version 0.3.0.
* Responsive layout: all seven example buttons remain usable at 420 px with no horizontal document overflow. The temporary viewport override was reset. No browser warning/error logs were recorded for the final checks.
* Static checks: focused Ruff correctness checks, Python compilation and JavaScript syntax passed.
* Reference comparisons: **156/164 evaluated entries within unchanged tolerances; 8 differences; 0 missing in solved models**. Sample 5 has no reference. Sample 7 has 40 blocked reference entries because its original model is unstable.
* Provenance: both new STD files and both original output files match `Sample 6.zip` byte-for-byte. Sample 6's normalized JSON is a separate reproducible copy. Original support/release lines have not been changed.

A freshly extracted portable archive imported the packaged engine, ran Sample 6 and reproduced properties, casement profiles and physical response exactly. Its CLI rejected the original Sample 7 with the same affected-node diagnostic. The check used installed project dependencies; dependencies are not bundled. Evidence: `validation/package-smoke.json`. Read `SAMPLE_6_7_VALIDATION.md` for the support/release issue and the user's confirmed support policy. Test success does not establish universal accuracy, certification or superiority to STAAD.Pro.

## Documentation publication checks

The September 2026 repository publication adds the 26-page implementation and industry guide, editable Markdown, its PDF builder and a runnable cantilever tutorial. All 35 application/engine files recorded in `package-smoke.json` still match their tested SHA-256 values. The full 234-test suite was not rerun for these documentation and packaging changes.

The tutorial's primary case gives 4 kN-m and 2.666666667 mm; its factor-two combination gives 8 kN-m and 5.333333333 mm. The exact Python example printed in the guide was executed and returned the expected 1000 N vertical reaction, 4000 N-m support moment and 0.001 rad tip-rotation magnitude. Both original Sample 6/7 models and output files retain their recorded hashes. Publication checks verified the staged source bytes and excluded local runtime/environment files. The final PDF has 19 chapter bookmarks; all 26 pages were rendered and visually reviewed. Evidence: `validation/publication-check.json`.
