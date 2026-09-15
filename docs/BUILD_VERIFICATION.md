# Build verification - engine v0.3.0, Sample 7 revision r1

Retested on Windows with Python 3.14, 15 September 2026.

* **Full suite: 244 passed, 1 licensed-oracle test skipped, 0 failed**, in 164.99 seconds. Eight dependency/deprecation warnings remain. Evidence: `validation/retest.xml`.
* **Real HTTP server: 15 workflow groups passed.** Samples 1-6 and the approved Sample 7 revision complete analysis. Original Sample 7 still fails with its loaded-mechanism diagnostic and retains geometry. Checks include upload, progress, all six revised cases, exact exported result comparison, malformed requests, upload limits, repeatability, queued/running cancellation and server responsiveness. Evidence: `validation/e2e.json`.
* **Approved revision:** only the start FX releases on members 5, 16, 27 and 38 were removed. All supports and other model records are unchanged. Nine new numerical/provenance cases check independent dead-load balance, the four-connection cut, all six case restraints and releases, and wind transfer to both support groups. One new API case checks separate original/revised downloads and successful revision analysis.
* **Gravity path:** the pins balance 26.159769123 kN of dead load. The four restored axial connections carry 11.994596768 kN from the lower assembly. Other supports have zero vertical reaction in all six cases. MY/MZ remain released at the four changed starts; the four untouched start FX releases remain free.
* **Browser:** the 07R example completed in 27.38 seconds, displayed 48 nodes/68 members/20 supports and cases 1-6. Physical/legacy switching, 3D controls, labels and JSON export were exercised. The downloaded JSON exactly matches direct results for properties, casement and physical_response. All eight example controls fit a 420-pixel viewport without horizontal document overflow; the temporary viewport override was reset. No browser warning/error logs were recorded.
* **Reference evidence remains separate:** 156/164 original evaluated entries pass unchanged tolerances; eight differences remain. Sample 5 lacks a reference. Original Sample 7 has 40 blocked entries. The revised model has no matching independent reference; its numerical checks are not added to the reference-pass count.
* **Source scope:** engine code is unchanged from the September 13 numerical build. Application changes only add the explicit revised-example download and menu choice. Original Sample 6/7 source files and reference hashes are preserved. New revision provenance is in `examples/revisions/sample7/source.json`.
* **Static checks:** focused Ruff correctness checks passed for the changed application, verification and packaging code.

The revised peak absolute displacement is 121.328821773 mm; the member chord-relative peak is 50.826810234 mm. These are response values requiring project-specific serviceability and linear-theory applicability review. Capacity, code compliance and superiority to STAAD.Pro have not been established. See `SAMPLE_6_7_VALIDATION.md` and `validation/sample7-revision-check.json` for the detailed actions and checks.

The portable package is checked from an extracted copy, with exact result comparison for Sample 6 and the approved revision and expected rejection of original Sample 7. It uses installed project dependencies, which are not bundled. Evidence: `validation/package-smoke.json`.

## Earlier evidence

The September 13 baseline recorded 234 passing tests, one skip, 14 HTTP groups, the original 26-page guide and the cantilever tutorial checks. Those records are preserved under `validation/history/2026-09-13`. They describe the earlier source and must not be confused with this revision's totals. Current publication and document checks are in `validation/publication-check.json`.
