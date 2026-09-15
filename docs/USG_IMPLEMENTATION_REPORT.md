# Fully unitized USG implementation and use guide

Original v0.3.1 implementation report, September 15, 2026. Scope: only the six files in USG_1.zip. For the current v0.3.2 corrections, refreshed tests and requirement status, read [USG_REAUDIT_REPORT.md](USG_REAUDIT_REPORT.md) and [USG_REQUIREMENT_AUDIT.md](USG_REQUIREMENT_AUDIT.md).

The user requested a rollback of all earlier September 15 work, including the approved Sample 7 revision. Commit 37e95e8 implements that rollback and restores the September 13 source tree. The current changes are a new, USG-only implementation on that restored baseline. The CAD/MAAS extractor, its dependencies and the Sample 7 revision are excluded. Original Sample 7 remains unchanged and is used only as an existing regression fixture.

## 1. Root cause

The confirmed generic defect is profile classification by PRIS property row order. The old classifier interprets the first property rows as particular facade roles, using a restricted property pattern. Reordering identical assignments or assigning separate properties to members can therefore change profile envelopes even though the structural model is unchanged.

The correction classifies the actual canonical model by geometry and released splice topology, with a complete explicit role mapping for other conventions. It does not use filenames, known node/member IDs, sample property values, bay counts or panel counts to choose results.

The observed major moment differences have strong evidence of a reporting-station cause. The reference point values closely agree, while a continuous extremum occurs at a different station. The displacement differences remain at the same reference station; their exact first numerical cause cannot be identified from envelope-only JSON. No arbitrary correction was applied to stiffness, loads or displacement recovery.

## 2. Evidence and input fidelity

All six source files are copied byte for byte and checked against SHA-256 in `validation/usg-source-manifest.json`: USG_1.std, USG_2.std, both result JSON files, the enhancement text prompt and its 67-page reference PDF. The ZIP hash is `b88f72dba1a79856b19a013fc2f4d2a96c57459d5f040e9fffbb65a39ceb0dab`.

The supplied PDF repeats the text requirements and adds four screenshots on its final three pages; these were inspected. The screenshots show adjustable bays, panels, segment/stack dimensions and profile choices. The actual production NextGen frontend, STD generator and profile database are absent from the supplied files and this repository. A screenshot is insufficient to reconstruct their authoritative derived-profile formulas.

The restored engine was run on both models before production edits. Those baseline results and comparisons are retained in `validation/usg-baseline`. The pre-implementation architecture, hypotheses and test plan are in `docs/USG_RESEARCH_PLAN.md`.

| Model | Nodes | Members | Geometry | Cases |
|---|---:|---:|---|---|
| USG_1 | 40 | 57 | Three 1.2 m bays; 14.4 m height | DL, WL, DL+WL, DL-WL |
| USG_2 | 44 | 64 | Three 1.3 m bays; 15.8 m height | DL, WL, DL+WL, DL-WL |

The STD files define the actual units, properties, loads, supports and releases. The software preserves those inputs. Numerical outputs are not read from the expected JSON during analysis; only the validation runner reads that JSON to compare results.

## 3. Files and methods changed

| File | Purpose |
|---|---|
| engine/profiles.py | New classify_unitized method; geometric/splice roles and validated explicit member-role mapping |
| engine/adapter.py | Use the new classifier for fully_unitized; add analysis.profile_classification |
| engine/comparison.py | Preserve metadata, normalize stations, expose reference-point error, guard near-zero relative error and reject unavailable/out-of-range stations |
| engine/__init__.py | Identify the USG-only release as 0.3.1 |
| app/console.py | Provide the two original USG example downloads and the measured USG comparison report |
| app/web/index.html | Add U1/U2 controls, report link and release label |
| scripts/validate_usg.py | Generate all-field before/after reports and member/node diagnostics |
| scripts/retest_e2e.py | Include both USG examples and their report in real HTTP regression testing |
| tests/test_unitized_customization.py | Structural customization, role stability, units, load/property sensitivity and invalid mappings |
| tests/test_usg_evidence.py | Source hashes and comparison/metadata behavior |

There are no changes to the core frame stiffness, parser, load assembly, solver or recovery equations in this release. Original OpenSTAAD extraction remains separate. The existing job API and the `properties`, `profiles` and `physical_response` structures remain compatible; classification diagnostics are additive.

## 4. Engineering explanation

The existing engine represents a prismatic 3D linear elastic frame with six DOFs per node. It reads the STD into a canonical model in metres, newtons and pascals, forms local axes including BETA, assembles consistent loads, condenses released DOFs, solves primary cases and superposes combinations. Full physical displacement includes shear deformation. Legacy envelope fields retain their existing section-recovery/reporting definitions.

The new automatic role convention is global Y vertical with one facade assembly. Vertical members become mullions. Horizontals at the minimum and maximum assembly Y become sill and head respectively. Interior ends of vertical members released in axial FX and bending MY/MZ identify stack-splice elevations. Horizontal members at those elevations become stack members; other horizontals become transoms.

This rule uses model geometry and connectivity rather than PRIS formatting. Its assumption is returned with the result. It does not claim that every possible facade connection convention has the same semantic roles. Inclined members require an explicit mapping. Other conventions can supply the complete `profile_member_ids` map in the existing generation-request JSON.

The mapping must cover every member exactly once using known roles: mullion, stack, head, sill and transom. Unknown roles, nonexistent IDs, duplicate memberships, boolean/string IDs and incomplete coverage are rejected. Explicit mapping defines reporting groups; it does not alter section stiffness or loads.

Comparisons keep numerical agreement separate from governing metadata. Stations in inches and metres are normalized before comparison. Endpoint flags are not treated as physical lengths. Missing, nonfinite or out-of-range stations are not silently converted into the nearest valid location. A near-zero reference has an absolute error and an unavailable relative error instead of an unstable percentage.

## 5. Before versus after

The existing broad tolerance is the larger of 0.1% relative error and an absolute floor: 0.01 kN for force, 0.01 kN-m for moment, 0.05 mm for displacement. Both USG models pass all 28 numeric fields under those unchanged limits. A stricter investigation uses the larger of 0.01% and 0.00002 kN/kN-m or 0.001 mm. Each model has 20 passing fields and eight differences under that stricter test.

| Metric | Reference | Before | After | Absolute error |
|---|---:|---:|---:|---:|
| USG_1 global major moment, kN-m | 3.539295 | 3.547253 | 3.547253 | 0.007958 |
| USG_1 global displacement, mm | 31.987816 | 32.030124 | 32.030124 | 0.042308 |
| USG_2 global major moment, kN-m | 10.042384 | 10.043964 | 10.043964 | 0.001580 |
| USG_2 global displacement, mm | 165.864593 | 166.009154 | 166.009154 | 0.144561 |

The supplied models keep the same numerical values after the classifier correction; their existing role assignments were in the expected order. The improvement appears when equivalent property assignments are reordered or customized. A regression verifies that the complete properties, profile counts and physical response remain identical when PRIS rows are reversed.

At the reference moment stations, calculated values are approximately 3.539298306 and 10.042387850 kN-m, only a few millionths from the reference. That is different from claiming that the continuous peak has the reference value. Reducing a continuous peak to match a sampled value would change the result definition.

The full 56-field table in `docs/USG_VALIDATION.md` includes reference, before, after, absolute/relative error and strict status, followed by reference/actual members or nodes, normalized stations, load cases and location status. The machine-readable comparison additionally contains axes, direction, source, reference-point value and metadata differences. No field missing from the source is invented.

## 6. Tests added

Twenty-four USG-focused test cases cover:

- One, two and five unequal bays with different panel counts and segment lengths.
- Stack gaps of 0.07, 0.12 and 0.25 m rather than one assumed splice dimension.
- Individual per-member PRIS assignments in reverse role order.
- New node/member IDs, translated geometry and reversed member directions for role classification.
- Nonstandard load-case IDs and linear combination superposition.
- Independent wind resultant versus support reactions, and zero vertical reaction at wind-only supports.
- Doubled elastic modulus producing half displacement, changed inertia reducing movement, and wind scaling by 1.75 while gravity stays unchanged.
- Equivalent PRIS values specified in cm and mm producing equal canonical properties and response.
- Exact complete envelope invariance under PRIS reordering.
- Complete explicit role mapping and rejection of invalid mappings.
- Six original source hashes and robust comparison behavior.

These independent model generators are test fixtures. They do not constitute implementation or verification of the absent production NextGen generator. The full engine suite additionally covers analytical beam/frame solutions, loads, releases, instability, units, physical extrema, existing extraction contracts and application behavior. Current totals and execution evidence are in `docs/BUILD_VERIFICATION.md`.

## 7. All-sample validation status

USG_1 and USG_2 both solve their two primary cases and two combinations. Standard numerical comparisons are 28/28 per model. Strict comparisons are 20/28 per model, with eight explicit differences each and no missing metric values. A software test that expects and checks a documented difference is not a claim of exact reference equivalence.

`validation/usg` contains each actual result, selected-member diagnostics, all-node diagnostics and full comparison JSON. Member evidence includes local axes, section/material values in SI, release DOFs, local end displacements and forces, loads and 13 inspection stations. Node evidence includes coordinates, restraints, six displacement/rotation components and reactions for every case. The inspection station grid is for diagnosis only; it does not replace continuous physical extrema.

`scripts/validate_usg.py` reproduces the report without modifying expected data. `tests/test_usg_evidence.py` verifies the source files remain byte-identical. Input hashes, baseline commit and solver version identify the calculation provenance.

## 8. Other-flow regression status

The existing original Samples 1-6 retain their prior comparison outcomes: 156 of 164 evaluated fields meet the unchanged tolerances, with eight retained differences in Samples 1-3. Sample 5 has no reference. Original Sample 7 still fails because its lower assembly has no vertical gravity path through the specified releases; its 40 reference entries remain blocked. The reverted connection revision is not reintroduced.

The complete automated suite and real HTTP workflows exercise the existing casement/standard flows, original examples, queued/running cancellation, malformed input, progress and exported results. Only the fully unitized adapter selects the new role classifier. Numerical engine equations shared by the other flows are unchanged.

## 9. Remaining differences and next evidence

The USG global/mullion displacement differences are approximately 0.042308 mm and 0.144561 mm. The strict audit also retains small sill/transom differences. These have not been eliminated or masked by looser tolerances. Global and mullion reporting can repeat the same governing discrepancy, so field counts are not independent structural observations.

Prior investigations of full shear-inclusive recovery, the current legacy recovery, alternate moment-area reconstruction and shear coefficients did not prove the reference algorithm. [Bentley's joint-versus-section displacement explanation](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0112641) confirms that these outputs can treat shear differently, but does not specify enough to uniquely reproduce the supplied envelope results. This is evidence for further investigation, not permission to fit a correction.

The useful next evidence is the exact STAAD version and complete joint displacement, member section displacement/force and PRINT MEMBER PROPERTIES output for these same STD files. Those tables can reveal whether the first difference occurs in internal shear areas, joint response or section interpolation. Small equilibrium residuals by themselves cannot answer that question.

## 10. Known limitations

This release is a verified implementation within a stated scope, not a proof of 100% accuracy or superiority to STAAD.Pro. It does not perform design-code capacity checks, buckling, P-delta/nonlinear, shell/solid, contact or dynamic analysis. Supported syntax and resource limits remain documented in the existing engine manuals.

Explicit effective shear areas are preferred for custom sections. With YD/ZD and no AY/AZ, the existing engine uses Cowper's rectangular correction times AX. That approximation may not represent an arbitrary hollow extrusion's effective shear area. A drawing's dimensions and moments of area do not uniquely define those missing section properties.

The actual NextGen frontend-to-STD flow, derived male/female profile formulas, application property database and supported customization limits remain unverified because their source was not supplied. The production boundary tested here is the actual STD-to-analysis path. No screenshots or expected JSON are used to fabricate absent generator behavior.

The API keeps structural jobs in memory, so use one server process; restarting loses current job records. The local launcher uses localhost. A multi-user internet deployment would need its own authentication, durable storage and resource-isolation design.

## 11. Run and use the software

1. Extract the new source ZIP or clone the repository. Python 3.10 or newer is required; this release was tested on Windows with Python 3.14.
2. Double-click `start_api.bat` (or the top-level launcher in the ZIP). First setup downloads the Python dependencies into a local virtual environment. No CAD converter is required.
3. Open `http://127.0.0.1:8000`. Click **U1 Unitized - USG 1** or **U2 Unitized - USG 2**. The actual interface uses a middle dot in those labels.
4. Alternatively upload your own STD and choose **Fully unitized**. If the role convention differs, upload generation-request JSON containing a complete `profile_member_ids` mapping.
5. Wait for completion, check geometry, member/support counts, load cases and result definitions, and inspect the profile table.
6. Use the result selector to distinguish full physical response from legacy integration values. Do not compare a physical-response displacement with a legacy reference without accounting for the different definition.
7. Export the JSON and retain it alongside the exact STD and software version. Use the USG comparison-report link for the measured reference audit.

```powershell
.\.venv\Scripts\python.exe -m engine examples/usg/USG_1.std `
  --flow fully_unitized --output usg-result.json
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/validate_usg.py
.\.venv\Scripts\python.exe scripts/retest_e2e.py
```

The existing API remains `POST /jobs` with multipart `std_file`, `extraction_flow=fully_unitized` and optional `generation_request` JSON file; poll `GET /jobs/{id}` and retrieve `GET /jobs/{id}/result`. The additive `analysis.profile_classification` explains grouping. Known profile roles may be mapped with a JSON object such as `{"profile_member_ids":{"mullion":[1,2],"head":[3],"sill":[4]}}`, but those IDs are illustrative and valid only for a corresponding four-member model.

In industry this supports early facade stiffness studies, checks of generated STD models, profile-envelope quality control and regression testing after configuration changes. It can identify unintended releases or reporting groups and make model comparisons reproducible. It does not issue a construction approval or select an acceptable project movement limit. Those decisions require project criteria and independent engineering review.
