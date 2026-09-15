# Build verification - USG-only v0.3.1

Verified on Windows, Python 3.14, September 15, 2026.

- **258 tests passed, one licensed OpenSTAAD oracle test skipped, zero failures**, in 170.49 seconds. Eight existing dependency/framework deprecation warnings remain. Evidence: `validation/retest.xml`.
- **16 real HTTP workflow groups passed**, including both USG files, original models, assets/report access, upload/progress/results, repeatability, malformed inputs, size limits and queued/running cancellation. Evidence: `validation/e2e.json`.
- **24 USG-focused tests** were added: custom bays/panels/segments/stack gaps, separate/reordered PRIS assignments, role invariance under renumbering/translation/direction changes, load/material/property sensitivity, equivalent units, complete mapping validation, source hashes and quantitative comparison behavior.
- **USG references:** 28/28 numerical fields pass unchanged standard tolerances for each model. A stricter audit has 20 passes and eight differences per model. All 56 reference fields and governing metadata are retained in `docs/USG_VALIDATION.md` and `validation/usg/comparison.json`.
- **Original regressions:** 156/164 evaluated reference values pass, with the same eight prior differences. Sample 5 has no reference; original Sample 7 retains its loaded-mechanism rejection. No revised Sample 7 model is included.
- **Browser:** U1 completed in 13.87 seconds and U2 in 15.24 seconds. Physical/legacy switching, model view, member labels and JSON exports were exercised. Both saved downloads exactly match direct results for properties, profile counts and physical response. U2 reports 44 nodes, 64 members and 16 supports. No warning/error console logs were recorded.
- **Responsive check:** all nine original/USG example controls fit a 420-pixel viewport with document width 405 pixels; the viewport override was reset. Desktop and mobile screenshots are retained with `validation/usg-browser.json`.
- **Static checks:** full focused Ruff checks passed for the new classifier, comparison, USG tests and validation runner. Focused fatal/syntax/name checks passed for edited existing application/adapter/HTTP code. Existing style warnings in the legacy adapter were not represented as new defects.
- **Fidelity:** all six USG archive files remain byte-identical, verified by their manifest hashes. The STD defines actual geometry, PRIS, materials, loads, supports and releases. No expected JSON is used to generate numerical answers.

The classifier fixes property-order dependence; the original USG models keep their numerical values. Core stiffness, load and recovery equations are unchanged. The displacement differences of 0.042308 mm and 0.144561 mm remain in the legacy envelope comparison. Complete independent joint/section reference tables are needed to establish their first numerical cause. The actual NextGen frontend/generator source was not supplied.

All earlier September 15 changes were reverted at the user's instruction before this USG-only work. Rollback commit 37e95e8 exactly restores the September 13 source tree. The previous test/publication evidence is retained under `validation/usg-baseline/project`, distinct from this build. No CAD/MAAS extractor or dependencies are included.

See `USG_IMPLEMENTATION_REPORT.md` for the complete change report and use instructions, `validation/usg-verification.json` for the verified source snapshot, and `validation/package-smoke.json` for package extraction checks. Passing software tests do not establish complete reference equivalence, project design approval or superiority to STAAD.Pro.
