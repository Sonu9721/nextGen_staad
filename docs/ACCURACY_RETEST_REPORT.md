# Accuracy and end-to-end retest — Mini STAAD v0.2

The updated build passes **180 automated tests** and **12 real HTTP workflow groups**. One licensed OpenSTAAD test remains skipped. The four supplied reference files still contain eight value differences out of 124 checked entries, at the original tolerances. No tolerance was relaxed and no result was calibrated to a sample number.

## Corrections made

* Fixed a legacy import-path collision that prevented default test discovery.
* Rejected explicitly negative member-load distances and invalid multi-axis SELFWEIGHT abbreviations; these could previously be accepted or misinterpreted.
* Added finite-value and shape validation for the canonical model, and a structured input error for invalid text encoding.
* Replaced the absolute zero-stiffness threshold with a scale-aware check. Uniformly scaling material stiffness and loads now preserves the same valid solution over tested factors from 1e-18 to 1e12.
* Computed floor area relative to a panel origin, avoiding cancellation at large survey coordinates. The translated 1e8 m fixture preserves the same loads and area.
* Independently checked applied physical forces and moments against actual support reactions for each primary and combination case. Scaled backward error is checked separately for every primary case.
* Fixed stale metric details while starting a new job, preserved the completed model name for downloads, and repaired a malformed UTF-8 character in the interface source.

## More complete physical reporting

The new `physical_response` field searches continuous piecewise polynomial fields for axial force, shear, torsion, bending and resultant displacement peaks. It checks both sides of concentrated-force jumps and includes axial and Timoshenko shear deformation. This catches interior shear/axial peaks that can be zero at both ends and displacement peaks between the legacy sampling stations.

The interface defaults to physical response. Its selector also exposes legacy extraction values. The original `properties` and `casement` fields retain their established definitions for integration compatibility, and profile tables are tied to those definitions. The two sets of values measure different quantities and should not be compared without accounting for those definitions.

The independent virtual-work tests require relative agreement within 2e-10 or absolute agreement within 2e-14 m for the tested beam displacements. Subdivision tests compare one member with 2, 5 and 11 elements under the same physical loading. These tests are independent of the solver's stiffness and load-vector formulas. They validate specific mathematical cases, not arbitrary structural designs.

For example, the sample 4 physical resultant displacement is 17.443526323 mm; its legacy value is about 17.431 mm. Bentley's published explanation describes how joint and intermediate section displacement reporting can treat shear deformation differently. It does not provide a current, member-by-member reference for these models. [Bentley displacement explanation](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0112641).

## Remaining accuracy questions

* Samples 1–3 retain three underlying displacement differences, duplicated between global and mullion entries: about 0.064, 0.129 and 0.103 mm.
* Sample 2 has one underlying moment-envelope difference, duplicated between global and mullion entries. The reference moment is reproduced at its reported station; the analytical maximum lies elsewhere and is larger.
* Omitted shear areas are still inferred using the documented rectangular Cowper assumption when dimensions are present. Effective shear areas for arbitrary aluminium extrusions require independent confirmation.
* Sample 5 has no reference output. No licensed STAAD.Pro installation was used for a new direct run. The existing oracle test is opt-in and remains skipped.
* Linear prismatic frames are the supported scope. Shells, nonlinear analysis, dynamics, buckling and design-code/capacity checks remain unsupported and are not covered by these accuracy results.

A claim of 100% accuracy or superiority to STAAD.Pro is **not established**. The next decisive validation requires identical explicit material/section properties and loads, joint displacements, reactions, member-end forces and intermediate response from a licensed STAAD.Pro run, compared with independent analytical benchmarks.

## Evidence and reproduction

* `validation/retest.xml`: full automated test results.
* `validation/e2e.json`: fresh-server workflows and all five physical envelopes.
* `validation/comparison.json`: all 124 reference comparisons and governing stations.
* `VALIDATION_RESULTS.md`: comparison table and physical results for every sample.
* `tests/test_accuracy_audit.py`: reproducible independent and regression checks.
* `scripts/retest_e2e.py`: isolated server test with no Bentley process operations.

Run the verification commands in README.md. The strict reference-validation option intentionally fails while differences or missing references remain.
