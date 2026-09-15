# USG second audit and delivery report

Version 0.3.2. September 15, 2026. Starting revision: 09f5a7f, version 0.3.1.

**Decision: the supplied STD-to-result workflow is built and works in the tested scope. The complete 71-section product request is not yet verified as finished.** The actual NextGen generator is absent and strict reference differences remain. The second audit found and fixed three additional robustness problems, rather than merely repeating the old passing tests.

The previous rollback remains in force. This work uses only USG_1.zip as new input; no CAD/MAAS work or revised Sample 7 connection was restored. Original Samples 1-7 are retained solely as existing regressions. Publication of supplied documents/models remains pending the specific public-sharing approval requested previously; this delivery is local.

## 1. Research, breakdown and plan

All six ZIP entries were reopened and checked byte for byte. The entire 67-page PDF narrative was extracted and compared with the complete text prompt after PDF font-ligature normalization. It matches apart from a trailing screenshot marker. The four screenshots on pages 65-67 were rendered and visually inspected again.

All 71 numbered requirements are individually mapped to evidence and limits in [USG_REQUIREMENT_AUDIT.md](USG_REQUIREMENT_AUDIT.md). The pre-edit research, hypotheses, independent reproductions and execution sequence are in [USG_REAUDIT_PLAN.md](USG_REAUDIT_PLAN.md). Existing architecture A-T is retained in [USG_RESEARCH_PLAN.md](USG_RESEARCH_PLAN.md).

The audit traced the parser, canonical geometry/properties, unit conversions, loads, supports, releases, matrix solution, physical/legacy recovery, profile classification, governing selection and job API. The actual NextGen frontend, production STD generator and profile database are absent. Common Bentley installation locations and the OpenSTAAD COM registration were not found on this host; no licensed live oracle was run.

Fresh before-edit runs of both USG pairs are preserved under validation/usg-reaudit/before, in addition to the earlier v0.3.0 baseline. The original references remain unchanged.

## 2. Root causes, evidence and engineering corrections

### Interior bending peak depended on load magnitude

A 4 m simply supported beam under a uniform 1e-12 N/m transverse load has an absolute peak bending moment qL^2/8 = 2e-12 N-m at 2 m. The former extrema routine returned only endpoint roundoff (approximately 3.23e-27 N-m for local Y loading).

The first incorrect stage was coefficient trimming during root finding. A unit-sized absolute floor removed the linear shear coefficient at very small loads, eliminating the interior root. The correction normalizes the polynomial by its own nonzero coefficient scale before relative trimming. Multiplying the load cannot change a polynomial's roots; no section stiffness, load or expected value is altered. Both bending axes are tested at 1e-12, 1e-6, 1e3 and 1e12 N/m against the independent closed form.

### Endpoint displacement entered the force evaluator

A member-end displacement reference with axis RESULTANT was sent to the FX/FY/FZ/MX/MY/MZ lookup, raising ValueError. The corrected comparison first resolves the endpoint position and then selects translation for displacement or end force for force metrics. Global resultant, chord-relative casement and the existing transom support-subtraction definitions remain distinct.

Independent cantilever checks use PL^3/(3EI), including its zero fixed-end and zero chord-relative endpoint values. Nodal reference displacement and invalid force-axis behavior are also checked.

### Invalid reference stations were inconsistently handled

Nonfinite stations could survive normalization, malformed strings could throw, and a boolean could become distance 1. Station conversion now rejects missing, malformed, nonfinite, boolean or unknown-unit metadata as unavailable. Invalid nonfinite metadata is retained visibly as text so the audit can serialize valid JSON. It is never replaced with a plausible zero result. Unknown force axes also remain unavailable.

The initial reproducer had **13 failing and 7 passing cases**. After correction, all 20 pass; a full renamed-STD pipeline test adds the 21st second-audit case. Before-failure evidence is preserved in validation/usg-reaudit/reproduced-before.xml. These are regression fixtures, not failures of the delivered build.

The comments describing legacy section interpolation were corrected to distinguish an implemented compatibility approximation from a proven OpenSTAAD formula. [Bentley's explanation of joint and section displacement](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0112641) supports investigating shear treatment, but does not specify the exact algorithm needed to eliminate the supplied differences. No new displacement correction factor was introduced.

## 3. Files, methods, reasons and coverage

| File | Class / method | Change and reason | Verification |
|---|---|---|---|
| engine/recovery.py | MemberResult.extrema | Normalize coefficients before trimming so moment root locations do not depend on force scale; clarify compatibility comments | AUDIT closed-form both-axis scale cases; complete ENGINE regressions |
| engine/comparison.py | station_in_metres, _metadata_value, reference_point_value, compare | Correct endpoint quantity selection and unavailable station/axis diagnostics; preserve serializable invalid metadata | Endpoint/global/casement/transom/nodal and malformed-station regressions |
| tests/test_usg_reaudit.py | 21 parameterized cases | Add independent failure reproductions and full renumbered STD-to-payload test | Before failures, focused after pass, complete suite |
| tests/test_unitized_customization.py | custom_unitized fixture | Accept node/member numbering offsets for true regenerated STD files | Renumbered full output and original customization suite |
| scripts/retest_e2e.py | main | Submit complete/incomplete explicit mappings through real HTTP | 18 workflow groups; identical valid mapped output and structured nonretryable rejection |
| scripts/validate_usg.py | main/progress | Record parser, solver/recovery and envelope times plus phase boundaries | Both USG runs and saved comparison metadata |
| scripts/package_build.py | main | Current audit guide/status in launcher notes; current evidence/source checks | Archive integrity and extracted-code smoke check |
| engine/__init__.py; app/web/index.html | Version marker | Identify corrected build as 0.3.2 | HTTP version, browser footer, package imports |
| README.md; docs/BUILD_VERIFICATION.md; docs/KNOWN_LIMITATIONS.md; docs/USG_IMPLEMENTATION_REPORT.md | Documentation | Point to current second audit, counts and remaining limits | Final source/evidence review |
| docs/USG_REAUDIT_PLAN.md; USG_REAUDIT_REPORT.md; USG_REQUIREMENT_AUDIT.md | Reports | Research before edits, measured result and all 71 requirement statuses | Requirement IDs checked for complete unique coverage |
| validation/usg-reaudit; validation/usg; validation/*.json, *.xml, screenshots | Evidence | Preserve baseline/failures separately and refresh current results, tests, browser and source hashes | Original source hashes; numerical before/after equality; current package checks |

No change was made to stiffness construction, load assembly, the linear solution, section properties or actual displacement equations. The earlier property-order classifier correction remains in force.

## 4. Actual model and customization matrix

| Characteristic | USG_1 | USG_2 |
|---|---|---|
| Nodes / members / supports | 40 / 57 / 16 | 44 / 64 / 16 |
| Bay widths, m | 1.2, 1.2, 1.2 | 1.3, 1.3, 1.3 |
| Total height, m | 14.4 | 15.8 |
| Distinct Y levels, m | 0, 1.4, 4.66, 4.8, 6.2, 9.46, 9.6, 11, 14.26, 14.4 | 0, 1.4, 4.66, 4.8, 6.2, 9.46, 9.6, 11, 14.8, 15.66, 15.8 |
| Mullion / stack / head / sill / transom counts | 36 / 6 / 3 / 3 / 9 | 40 / 6 / 3 / 3 / 12 |
| Primary / combined cases | 1, 2 / 3, 4 | 1, 2 / 3, 4 |
| Numerical property source | Five original PRIS assignments in STD | Five original PRIS assignments in STD |

The independent customization tests cover one, two and five unequal bays; one, two and three panels; unequal segment lengths; stack gaps of 0.07, 0.12 and 0.25 m; separate/reversed PRIS rows; new section stiffness; changed wind; equivalent cm/mm properties; translated/reversed geometry; and complete explicit roles. The new full-pipeline test adds 5000 to every node/member ID in two generated STD files and verifies complete numeric results and correctly translated governing IDs with cases 7, 13, 21 and 22.

These independent fixtures verify the solver's data-driven behavior. They do not replace testing the actual production generator or establish its supported limits.

## 5. Before/after supplied references

| Model / quantity | OpenSTAAD reference | v0.3.1 before | v0.3.2 after | Absolute error |
|---|---:|---:|---:|---:|
| USG_1 global major moment, kN-m | 3.539295 | 3.547253 | 3.547253 | 0.007958 |
| USG_1 legacy global displacement, mm | 31.987816 | 32.030124 | 32.030124 | 0.042308 |
| USG_2 global major moment, kN-m | 10.042384 | 10.043964 | 10.043964 | 0.001580 |
| USG_2 legacy global displacement, mm | 165.864593 | 166.009154 | 166.009154 | 0.144561 |

Both models' complete properties, profile counts and physical response are unchanged by the second-audit fixes. The new defects occur outside the original sample conditions; no improvement of their displacement match is claimed.

Each model passes 28/28 numerical fields under unchanged historical tolerances (max of 0.1% and 0.01 kN, 0.01 kN-m or 0.05 mm). The stricter investigation (max of 0.01% and 0.00002 kN/kN-m or 0.001 mm) gives 20 passes and 8 differences per model. These thresholds are regression and investigation policies, not project design acceptance limits. Complete values and metadata remain in USG_VALIDATION.md and validation/usg/comparison.json. The fresh v0.3.1 full payloads remain in validation/usg-reaudit/before.

## 6. Tests, E2E and final review

- **279 passed, 1 skipped, zero failures; 189.07 seconds.** Eight existing framework deprecation warnings remain. The skipped test requires an enabled licensed OpenSTAAD oracle. Evidence: validation/retest.xml.
- **18 real HTTP workflow groups passed.** This includes all nine examples, upload/progress/results, report assets, explicit USG role mapping, invalid mapping rejection, malformed requests, repeatability, limits and queued/running cancellation. Evidence: validation/e2e.json.
- **Original regressions:** 156/164 evaluated reference values pass; the same eight prior differences remain. Sample 5 has no reference. Original Sample 7 is still rejected for its loaded vertical mechanism; its 40 reference quantities are not claimed verified.
- **Browser:** U1 completed in 13.23 s and U2 in 14.99 s. Both downloaded JSON wrappers were reopened and their properties, profiles and physical_response matched direct solver output exactly. Physical/legacy switching, 3D and member labels worked. No warning/error console logs were recorded.
- **Responsive view:** 420-pixel viewport, 405-pixel document width and all nine example controls; override reset afterward. Browser evidence and screenshots are retained in validation/usg-browser*.
- **Static/dependency review:** focused full Ruff checks pass for comparison, new tests and USG validator; fatal/syntax/name checks pass for the edited existing recovery and HTTP runner. pip check reports no broken requirements. Original six source files remain byte-identical.
- **Package:** build, extraction, source hashes and rerunning both models are checked by scripts/package_build.py and scripts/smoke_usg_package.py; current evidence is validation/package-smoke.json. Dependencies are installed locally, not bundled. Extraction testing uses the existing tested virtual environment and is not a fresh-machine installation test.

## 7. Measured performance

| Phase, seconds | USG_1 | USG_2 |
|---|---:|---:|
| Parse and validate | 0.011 | 0.006 |
| Assembly, solve and recovery | 0.544 | 0.612 |
| Profile and physical envelopes | 11.498 | 13.571 |
| Total direct analysis | 12.052 | 14.189 |

These are observations on the current Windows/Python 3.14 host during concurrent validation, not guaranteed latency benchmarks. Solver phase boundaries distinguish stiffness, load generation, solve and recovery, but factorization is not separately profiled. Envelope work dominates these two models; this does not justify replacing the small dense matrix solver with sparse machinery. Maximum supported model size was not load-tested in this audit.

## 8. Remaining differences and limitations

The exact source of the 0.042308/0.144561 mm legacy displacement discrepancies remains unresolved. The references are envelopes, not complete joint/section tables. Obtain the exact STAAD version, PRINT MEMBER PROPERTIES, joint displacements/reactions and member section forces/displacements to compare the earliest stage. Moment values at the reference stations agree closely; continuous peaks occur at other stations.

The second audit also repeated recovery hypotheses at the exact reference stations. For USG_1, full physical, existing legacy and curvature integrated with a linear endpoint closure give 32.056906, 32.030121 and 32.021986 mm, versus 31.987816 mm. For USG_2 they give 166.100894, 166.009147 and 166.006738 mm, versus 165.864593 mm. None establishes a match. Small differences also occur at the supplied reference head nodes (2.982088666 versus 2.982192 mm; 12.253554291 versus 12.253715 mm), so changing only span interpolation cannot explain every difference. These diagnostic alternatives were not put into production. Per-profile observations are preserved in validation/usg-reaudit/recovery-investigation.json.

The complete NextGen input-to-STD workflow is unverified without its source. Automatic facade roles assume global Y vertical, one assembly and horizontal stack members at released axial splice ends. Other role conventions require a complete explicit mapping; that does not make unsupported sloped/nonrectangular FLOOR LOAD geometry supported.

Legacy axial reporting applies to inner vertical members. A single-bay assembly has no inner mullion, so that compatibility value may be empty; full physical FX still covers all members. Legacy transom displacement subtracts the smaller endpoint nodal magnitude and is not the same as chord-relative displacement. The UI and reports retain these definitions.

Supported syntax and exclusions are listed in SUPPORTED_STAAD_FEATURES.md. This build remains linear elastic with prismatic frames, a 1000-node/2000-member dense-solver limit and in-memory single-process jobs. It does not cover distributed member moments, arbitrary floor regions, section libraries, springs/settlements, nonlinear/P-delta/dynamics, design-code capacity or construction approval. Assumed shear areas for arbitrary hollow sections require independent justification. No claim of 100% accuracy or superiority to STAAD.Pro follows from these tests.

## 9. Run and use

1. Extract MiniSTAAD_v0.3.2_Retested.zip. Install Python 3.10+; this build was tested with 3.14.
2. Double-click Start MiniSTAAD.bat; first setup downloads dependencies. Open http://127.0.0.1:8000.
3. Choose U1 or U2, or upload an actual STD and select Fully unitized. Review geometry/supports and analysis status.
4. Use the physical/legacy selector deliberately, inspect governing members/cases and export the result JSON with its source STD.
5. For a different role convention, provide generation-request JSON with complete profile_member_ids. Invalid/incomplete maps fail explicitly.
6. Reproduce checks using pytest, scripts/validate_usg.py and scripts/retest_e2e.py. Consult the complete requirement matrix and comparison report before treating this as a production replacement.

The currently running review server uses port 8012 so it can coexist with earlier local previews. It is not a public deployment. Industry uses within this scope include facade stiffness studies, generated-model QA, repeatable profile-envelope comparison and regression checks. Project movement limits and design approval remain separate engineering decisions.
