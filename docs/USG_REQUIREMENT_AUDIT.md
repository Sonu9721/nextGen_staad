# USG requirement-by-requirement audit

The complete original prompt has 71 numbered sections. This matrix records the result of the second audit of v0.3.2. **The full requested product is not yet verified as complete.** The supplied STD-to-result workflow is runnable and tested; the actual NextGen generator and exact displacement equivalence remain open.

**Checked** means reviewed and supported by the stated finite tests or evidence within the documented STAAD subset. It does not prove every possible structural model. **Partial** means useful implementation/evidence exists but an explicit part remains unresolved. **Unavailable** means required source or independent evidence was not supplied. These labels are not numerical accuracy percentages.

Evidence abbreviations: **CUSTOM** = tests/test_unitized_customization.py; **AUDIT** = tests/test_usg_reaudit.py; **ENGINE** = tests/test_engine.py, test_engine_extended.py, test_accuracy_audit.py and test_member_moments.py; **HTTP** = scripts/retest_e2e.py and validation/e2e.json; **USG** = docs/USG_VALIDATION.md and validation/usg; **SOURCE** = validation/usg-source-manifest.json and tests/test_usg_evidence.py.

| # | Original requirement | Status | Evidence and remaining boundary |
|---:|---|---|---|
| 1 | Current reference flow | Partial | STD-to-result path verified. Real NextGen frontend/generator and live STAAD-to-OpenSTAAD run unavailable. |
| 2 | Known problem | Partial | USG measures all fields; moment station evidence is strong, displacement first divergence remains unresolved. |
| 3 | User-customizable models | Partial | CUSTOM and AUDIT cover representative variations; actual application's complete input limits are absent. |
| 4 | Variable number of bays | Partial | One, two and five unequal bays tested. Actual supported frontend limits not established. |
| 5 | Variable number of panels | Partial | One, two and three panels tested with changing connectivity. Frontend limits unavailable. |
| 6 | Custom panel/segment dimensions | Partial | Parsed coordinates drive lengths; unequal segments tested. Exact production UI field derivation unavailable. |
| 7 | Custom PRIS/member properties | Checked | Actual per-member AX/IX/IY/IZ and optional AY/AZ/YD/ZD drive calculations; CUSTOM sensitivity tests. |
| 8 | Custom profile assignments | Checked | Reordered/separate PRIS and complete explicit role mapping tested; mapping does not change stiffness. |
| 9 | Derived profiles | Checked | Solver uses numerical properties in the STD. It does not recreate or claim to verify absent UI derivation formulas. |
| 10 | STD is structural source of truth | Checked | Canonical parser/model pipeline; SOURCE byte checks. No frontend defaults substitute for STD data. |
| 11 | No fixed topology | Checked | CUSTOM changes member/node counts and topology; canonical maps drive assembly. |
| 12 | No hardcoded member IDs | Checked | Full renamed-STD payload test in AUDIT; no sample IDs select solver behavior. |
| 13 | No hardcoded node IDs | Checked | Full renamed-STD test and translated/reversed-role test; loads/restraints follow actual IDs. |
| 14 | Member/profile classification | Partial | Generic geometric/released-splice convention plus complete explicit mapping. Automatic semantic inference is limited to one Y-vertical assembly. |
| 15 | Dynamic loads | Checked | Actual joint, full/partial UNI, CON, CMOM, selfweight and supported FLOOR loads; ENGINE/CUSTOM. Unsupported loads explicitly fail. |
| 16 | Load cases | Checked | CUSTOM/AUDIT use 7 and 13 instead of 1 and 2; adapter selection and metadata checked. |
| 17 | Load combinations | Checked | Cases 21/22, superposition and combination exclusion tested; all selected cases feed envelopes. |
| 18 | Structural element formulation | Checked | 12 DOF Timoshenko frame; analytical, virtual-work, subdivision, torsion and transformation tests in ENGINE. |
| 19 | Timoshenko/Euler-Bernoulli behavior | Partial | Full physical recovery independently tested. Legacy section interpolation is not proven exactly equivalent to OpenSTAAD. |
| 20 | Section property investigation | Partial | PRIS unit/property sensitivity verified. Internal STAAD shear areas for the supplied hollow sections remain unavailable. |
| 21 | Local/global axes | Checked | BETA and spatial covariance tests, global/local forces and both bending axes in ENGINE. |
| 22 | Support conditions | Checked | Actual PINNED/FIXED/FIXED BUT restraints; gravity reaction at wind-only restraints checked. |
| 23 | Member releases | Checked | Condensation/recovery, supported six local release DOFs, axial releases and equivalent split-member tests. |
| 24 | Boundary implementation | Checked | Constrained DOFs, independent reactions, compatibility and mechanism rejection tests. |
| 25 | Numerical diagnostics | Checked | Scaled conditioning, eigenvalue, residual/backward error and force/moment equilibrium retained; not presented as reference proof. |
| 26 | Bending root cause | Partial | Reference-station moments reproduce closely; full independent section/end tables unavailable. Small-load extremum defect separately reproduced and fixed. |
| 27 | Internal member forces | Checked | Load integration, point-couple jumps, partial loads and independent exact solutions in ENGINE. |
| 28 | Stations/extrema | Checked | Polynomial roots and discontinuity sides; AUDIT fixes dependence on load scale. Legacy displacement sampling remains explicitly labeled. |
| 29 | Member-span deflection | Partial | Physical translation verified by virtual work and split models; exact legacy OpenSTAAD section recovery still unresolved. |
| 30 | Resultant displacement | Checked | Extractor trace confirms Euclidean translation magnitude; physical and compatibility definitions are disclosed separately. |
| 31 | Global displacement | Checked | Nodal and span maxima considered; independent interior peak test. Reference equivalence remains under sections 2/29. |
| 32 | Profile displacement | Checked | Same solved result filtered into roles; transom's existing support-subtraction rule retained and documented. |
| 33 | Global versus profiles | Checked | Legacy axial means inner vertical members and legacy shear uses ends; full physical response covers all members. These are not silently equated. |
| 34 | Units | Checked | Central SI normalization, imperial/mixed-unit cases, cm/mm PRIS powers and station unit comparisons. |
| 35 | Global equilibrium | Checked | Independent force/moment balances for each primary/combined case; computed about a stable origin. |
| 36 | Actual generator compatibility | Unavailable | NextGen frontend, profile database and production STD generator source absent; repository path requested. |
| 37 | Customization feature matrix | Checked | Both actual model dimensions/roles and representative variation dimensions are documented in USG_REAUDIT_REPORT.md. |
| 38 | Multiple samples | Checked | Both ZIP pairs run before/after and via HTTP/browser/package; original regressions retained. |
| 39 | Baseline before changes | Checked | v0.3.0 original baseline plus fresh v0.3.1 audit baseline and reproducing failures preserved separately. |
| 40 | Error metrics | Checked | Absolute/relative errors, near-zero N/A, normalized stations and separate metadata; invalid stations remain visibly unavailable. |
| 41 | Justified tolerances | Partial | Historical and stricter diagnostic thresholds remain unchanged and configurable. Project-specific engineering acceptance precision is not supplied. |
| 42 | No correction factors | Checked | No fitted moment/displacement multiplier. Changes follow root invariance and correct quantity selection. |
| 43 | No sample-specific logic | Checked | Model names only select supplied examples/validation inputs; no filename branch chooses equations or results. |
| 44 | No hardcoded expected results | Checked | Analytical test expectations are independent; expected JSON is used only for comparison. |
| 45 | First-divergence debugging | Partial | Two code layers reproduced precisely; supplied displacement divergence cannot be isolated from envelope-only references. |
| 46 | Diagnostics | Checked | Optional validation runner records per-member/per-node details, all cases, axes/properties/releases/loads/stations. Production result contract unchanged. |
| 47 | Engineering benchmarks | Checked | ENGINE includes axial, cantilever, simple/fixed beams, 2D/3D frames, partial/point/couple loads and extrema. |
| 48 | Regression tests | Checked | Every second-audit reproduced defect has a regression; before-failure and after-success evidence retained. |
| 49 | Other flows | Checked | Existing standard/casement/unitized regression suite and original models rerun; no unsupported spider-flow claim. |
| 50 | API/JSON compatibility | Checked | Existing job/result shape preserved; explicit-map and invalid-map HTTP checks added. |
| 51 | Result semantics | Checked | Existing envelope and adapter code traced; endpoint/member-span, force axes, resultant and transom semantics documented. |
| 52 | Profile envelopes | Checked | One structural solution, member filtering, all selected cases, governing metadata; full renumbered output invariant. |
| 53 | Discontinuities | Checked | Point forces/couples considered on both sides; inward endpoint limits verified by ENGINE. |
| 54 | Performance | Partial | Parser, assembly/solve/recovery and envelope times plus solver phase boundaries recorded. Factorization/internal subphase CPU profiling and maximum-scale performance qualification not performed. |
| 55 | Errors | Checked | Invalid/unsupported models, mechanisms, nonfinite structural inputs, invalid maps and recovery errors fail explicitly. Invalid reference metadata cannot invent a station. |
| 56 | Cancellation | Checked | Queued/running real HTTP cancellation and server responsiveness; in-house path leaves Bentley processes alone. |
| 57 | Architecture | Checked | Parser/model/units/loads/solver/recovery/classifier/adapter remain separate; no coupling to frontend objects. |
| 58 | Complete customization compatibility | Partial | Representative actual STD variations tested. Complete production UI space and generator require source. |
| 59 | Comparison table | Checked | All 56 reference numeric fields and governing IDs/cases/stations; machine-readable metadata/axis/source comparisons. |
| 60 | Root-cause report | Checked | USG_IMPLEMENTATION_REPORT.md and USG_REAUDIT_REPORT.md distinguish proven defects from unresolved differences. |
| 61 | Code-change report | Checked | File/method/change/reason/test coverage in both reports; second-audit changes itemized. |
| 62 | Limitations | Checked | SUPPORTED_STAAD_FEATURES.md, KNOWN_LIMITATIONS.md and both USG reports. |
| 63 | Definition of done | Partial | Available checks pass, but complete generator class, displacement equivalence and application acceptance remain open. |
| 64 | Hard constraints | Checked | Original sources unchanged; no artificial supports, empirical factors, fixed IDs, fitted results or unrelated CAD work. |
| 65 | Engineering principle | Partial | Generic canonical solution and independent tests follow the principle; exact supported production class is not yet proven. |
| 66 | First-divergence rule | Partial | Demonstrated before fixing extrema/comparison errors; missing independent section/nodal evidence blocks exact displacement diagnosis. |
| 67 | Research before coding | Checked | USG_REAUDIT_PLAN.md and fresh baseline/reproduced failures precede second-audit production edits. |
| 68 | Initial architecture analysis | Checked | A-T analysis in USG_RESEARCH_PLAN.md, extended by the second-audit plan; absence of generator explicitly disclosed. |
| 69 | Final validation | Checked | Full suite, all available pairs, custom variations, other flows, source checks, real HTTP and browser/package tests. |
| 70 | Final report format | Checked | Both USG reports provide root cause, evidence, files/methods, engineering, before/after, tests, all-sample/other-flow outcomes and limitations. |
| 71 | Final product goal | Partial | Runnable generic validation build delivered; exact reference-equivalent product for the entire NextGen input space remains unverified. |

To close the remaining product-level requirements, provide the actual generator repository and input constraints, plus the exact STAAD version, PRINT MEMBER PROPERTIES, joint displacements/reactions and member section force/displacement tables for these unchanged models. Those inputs identify the earliest numerical divergence without fitting expected envelopes.
