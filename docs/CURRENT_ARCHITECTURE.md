# Repository reconnaissance (before implementation)

The supplied archive is the extraction API, not the NextGen model generator. Its FastAPI job API stages a `.std` and optional JSON request, queues an in-memory JobRecord, and runs a spawned extractor process. `app/services/staad_runner.py` owns retry, timeout, host locking and process cleanup. `app/job_manager.py` owns progress, cancellation and retention. No frontend or `/analyze` route exists in the original repository.

## Dependency map

`extractor.run` parses topology, units and case identifiers, attaches to OpenSTAAD, opens the file, invokes analysis, then queries geometry, end forces, internal moment extrema, nodal translations and absolute/relative member deflections. Structural stiffness, section/material interpretation, releases and load generation are entirely delegated to STAAD. COM imports are already optional; the mathematical envelope functions can accept a compatible Python session.

The reusable path is `_extract_property_envelope` -> `_build_grouped_result_payload`. Standard returns absolute global envelopes. Fully unitized adds the same envelopes for ordered PRIS groups, using the existing geometry corrections in `profile_classifier.py`. Casement calls `casement_extractor.py` for signed force/moment extrema and chord-relative member deflection, plus selected nodal resultants. Its classifier additionally uses minority PRIS fingerprints; this extends the geometry-only description in the supplied brief. Classifiers must remain separate from the solver.

## Corrections and sample findings

* Samples 1-3 add `FIXED BUT FY MX MY MZ` supports and `START FX MY MZ` releases. Axial release condensation is required, not optional.
* Samples 1-3 are fully unitized references; sample 4 is a casement reference. Sample 5's output.txt is empty, so it cannot supply a numerical oracle.
* Four output.txt files contain complete JSON job responses, not text analysis listings.
* The API accepts the legacy executable path but validates that it exists, even though the runner never uses it.
* Empty axial envelopes retain `unit: kN`. Minor shear falls back to all members when the vertical set is empty. Preserve actual repository behavior.
* The original tests mock structural results. They establish contract/classification behavior, not solver correctness.

## Implementation sequence and touched files

First create a unit-aware parser and canonical model; then an independently tested 12-DOF spatial frame element, load assembly and recovery. Reuse envelope/classification functions through a numerical result adapter. Introduce backend dispatch in config/runner and isolate in-house abort/health from Bentley process handling. Preserve every existing endpoint. Compare all supplied references and record value and governing-location discrepancies before making any equivalence claim.

Do not implement design-code checks, nonlinear analysis, shells, solids, dynamic analysis, arbitrary STAAD commands, or an unrelated frontend. The principal unresolved risks are floor-panel detection/load transfer, BETA sign conventions, shear flexibility, hinge recovery, ill-conditioning and reference station sampling.
