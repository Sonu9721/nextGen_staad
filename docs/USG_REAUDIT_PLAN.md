# USG second audit: research and plan before production edits

Starting revision: 09f5a7f (v0.3.1). Scope remains the six unchanged files in USG_1.zip. The previous rollback remains in force. This audit does not publish supplied documents or models.

## Research and breakdown

All 71 numbered requirement sections were reread. The entire 67-page PDF was extracted; after normalizing font ligatures its narrative equals the text prompt, apart from a trailing screenshot marker. The four screenshots on pages 65-67 were rendered and inspected again. The two original STD/reference pairs and six source hashes were verified. Architecture A-T remains documented in USG_RESEARCH_PLAN.md; the parser, canonical model, element/load assembly, solution, recovery, adapter, legacy envelopes, classifier and current regression coverage were reviewed again.

The actual NextGen frontend, profile database and STD generator are absent. Common Bentley installation paths and the OpenSTAAD COM registration were not found locally. Neither screenshots nor envelope-only references establish exact production input limits or intermediate STAAD results. The generator path has been requested while independent verification continues.

Both original USG models were solved again before editing production code. Each retains 28/28 standard passes and 20/28 strict passes. This fresh baseline is preserved under validation/usg-reaudit/before. Existing moment-station evidence remains strong; the displacement first divergence remains unproven. Bentley's joint/section-displacement article was rechecked; it does not provide a complete exact interpolation algorithm.

## Reproduced defects and minimal fixes

1. A simply supported 4 m beam with a uniform 1e-12 N/m transverse load should have a 2e-12 N-m interior moment at 2 m. The v0.3.1 legacy extrema routine instead returns only endpoint roundoff. Its coefficient trimming uses an absolute unit-sized floor, discarding the shear root when load magnitude is small. Normalize coefficients by their own nonzero scale; preserve zero-polynomial handling. Test both bending axes across load scales against qL^2/8, independently of USG expected data.
2. A reference displacement at a member endpoint with axis RESULTANT enters the force-component lookup and raises ValueError. Resolve physical endpoint distance first, then evaluate translation for displacement and end force for force metrics. Keep chord-relative casement and legacy transom definitions. Test endpoint, nodal, invalid-axis and invalid-station behavior; unavailable diagnostics must remain explicit.
3. Complete the requirement-to-evidence matrix and capture performance by actual parser, solver and envelope phases. Expand HTTP coverage for complete explicit profile maps and invalid maps. Add a full-payload renumbering check; classification-only renumbering did not prove governing metadata propagation.

## Execution and acceptance

Add reproducing tests first and record their failures. Apply only justified corrections, rerun focused tests, then the full engineering/application suite, original sample audit, both USG audits and real HTTP workflows. Verify U1/U2 in the browser and exported results, then build and extract a fresh versioned ZIP. Check original source hashes and package source hashes. Update the root-cause, before/after, file/method/test coverage, limitations and all 71 requirement statuses.

Passing the available checks does not complete the absent frontend-to-STD integration or prove exact OpenSTAAD equivalence. Broad historical tolerances and stricter investigation thresholds remain unchanged; neither is a project design acceptance limit.
