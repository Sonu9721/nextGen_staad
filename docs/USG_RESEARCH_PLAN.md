# USG-only research and implementation plan

The user requested that all work from September 15 be reverted, including the earlier Sample 7 revision, and that work continue only from USG_1.zip. The rollback is commit 37e95e8; its tree exactly matches the September 13 baseline a646dd1. No CAD extractor, CAD dependencies, MAAS data or Sample 7 revision is part of this work. The six supplied USG files are preserved byte for byte in the source manifest.

## Repository and generator boundary

The repository contains a Python/FastAPI structural service and a browser model viewer. The actual NextGen configuration frontend, profile database and STD generator source are absent. The 67-page supplied PDF repeats the text requirements and adds screenshots; all were reviewed. Screenshots show bays, panels, segment/stack dimensions and derived profile choices, but cannot establish the underlying formulas or supported input limits.

The supported integration boundary is an actual generated STD file plus optional generation-request JSON. The STD remains the structural source of truth. Derived sections must arrive as the correct PRIS properties; the solver cannot reconstruct an absent production generator.

## Architecture relevant to the requirements

1. `engine/parser.py` reads coordinates, connectivity, PRIS properties, materials, BETA, supports, releases, member/joint loads, FLOOR LOAD, primary cases and combinations. `engine/units.py` normalizes the model to metres/newtons/pascals. Area and inertia use second/fourth powers of length conversion.
2. Dataclasses in `engine/model.py` hold nodes, members, per-member section/material/axis/release data, supports and loads. Validation rejects invalid properties, unsupported features and excessive model size.
3. `engine/element.py` constructs a 12-DOF Timoshenko frame, local/global transformations and consistent member loads. Released DOFs are statically condensed and recovered. Explicit shear areas take precedence; current default shear-area behavior needs direct STAAD property output to establish exact equivalence for these custom PRIS sections.
4. `engine/loads.py` generates consistent loads and rectangular floor tributaries. `engine/solver.py` assembles and scales stiffness, checks mechanisms, solves primary cases together and superposes linear combinations. It reports equilibrium and residual diagnostics and checks cancellation.
5. `engine/recovery.py` integrates end forces and applied loads to recover internal forces, translations and rotations. The full physical response includes shear; the existing compatibility recovery uses its historical section-displacement formula. `engine/physical.py` locates continuous extrema with load discontinuities.
6. `engine/adapter.py` passes solved results into the original envelope functions and preserves job/output contracts. The legacy classifier in `staad-max-extractor/profile_classifier.py` depends on PRIS property row order, a strict property regex and a limited role sequence. That is a confirmed customization defect independent of numerical stiffness.
7. API jobs and the original viewer already support cancellation, model display, result export and flow selection. This work will add only the two USG example choices and necessary unitized calculation/validation changes.

## Baseline and first divergence

The restored baseline is run against both unchanged files before production edits. USG_1 has 40 nodes/57 members, three 1.2 m bays and 14.4 m overall height. USG_2 has 44 nodes/64 members, three 1.3 m bays and 15.8 m height. Both have two primary cases and two combinations.

Earlier investigation of the same numerical baseline found major moments 3.547253/10.043964 kN-m versus 3.539295/10.042384 in the references, and legacy displacements 32.030124/166.009154 mm versus 31.987816/165.864593. These values will be reproduced in the new baseline evidence. Existing broad thresholds pass all 28 numerical fields per model; stricter investigation finds differences that must remain visible.

Ranked hypotheses and evidence:

1. **Confirmed profile-classification defect:** reordered or independently assigned PRIS lines can change role membership while geometry/stiffness are unchanged. Fix at the classifier using canonical geometry and released splice topology, with complete explicit role mapping for other conventions. No model IDs, coordinates, PRIS values or number of bays/panels will select production answers.
2. **Strong station evidence for major moments:** values at the supplied reference stations agree within a few millionths of kN-m. Continuous extrema occur elsewhere. Preserve continuous physical peaks; expose reference-point comparisons and governing metadata rather than invent a sampled envelope to match two files.
3. **Unresolved displacement recovery/shear-area equivalence:** differences persist at the reference station. Alternative shear coefficients and moment-area reconstructions did not establish a complete match. Complete reference joint/section tables and PRINT MEMBER PROPERTIES are needed to isolate the earliest numerical divergence. No fitted correction will be introduced.
4. **Less-supported hypotheses:** global unit, load or section-inertia errors would usually change the closely matching shear/axial/force results more strongly. Independent equilibrium, unit, stiffness and load variation tests will check these mechanisms; agreement in equilibrium alone does not prove STAAD equivalence.

## Minimal implementation and verification

Add a canonical unitized role classifier, retain an explicit complete member-role override, and return classification assumptions. Improve the comparison report with normalized stations, near-zero-safe relative error, reference-point values and separate metadata differences. Preserve stiffness, load and recovery equations unless an independent benchmark establishes a defect.

Add variable bay/panel/segment/stack, renumbering, reordered per-member PRIS, material/property/load variation, explicit mapping, unit conversion and source-integrity tests. Run both supplied references and all original regression flows. Test the actual HTTP upload/progress/result/export path and the browser's two USG choices. Save every reference field before/after with error, member, station, case and pass/difference status. Report missing generator integration and unresolved displacement differences explicitly.

## Primary sources

- [Bentley: joint versus section displacements](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0112641) describes nodal shear contributions and section moment-area recovery. It does not supply a complete exact OpenSTAAD interpolation formula.
- [Bentley: prismatic and general sections](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0113774) distinguishes analysis properties from additional shape information used in design.
- [Bentley: prismatic definition and printed properties](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0027695) describes dimension-based omitted properties and PRINT MEMBER PROPERTIES as evidence of internal values.

Sources checked September 15, 2026. The final measured report will be separate from this pre-implementation plan. This work does not claim universal accuracy or superiority to STAAD.Pro.
