# Sample 6 archive integration — v0.3.0

Both models from `Sample 6.zip` are included in the software and its example menu. The two original STD files and two original reference text files are preserved byte-for-byte. Each example folder contains provenance and SHA-256 hashes in `source.json`.

| Model | Nodes | Members | Outcome |
|---|---:|---:|---|
| Sample 6 | 44 | 61 | Analysis succeeds; all 40 supplied value comparisons pass the unchanged tolerances. |
| Sample 7 | 48 | 68 | Loaded vertical mechanism; analysis rejected. Its 40 supplied reference entries remain unverified. |

The original archive's SHA-256 is `6ef8b41a9e7251dfd95a62422138f8de07e3f107c8d89b3516a830700f70479c`.

## Sample 6 reference import and results

`examples/sample6/output.txt` lost all JSON quotes and colons before being supplied. `scripts/normalize_reference.py` reconstructs the line-oriented JSON into a separate `reference.json`, rejecting ambiguous syntax and duplicate keys. Numeric values are unchanged. Damaged path strings are preserved as received; missing characters are not reconstructed. This normalization is not enabled as a permissive service input parser.

The comparison uses the supplied casement extraction definitions, cases 1–4, and the same tolerance: max(0.1% of reference, 0.01 kN, 0.01 kN-m or 0.05 mm as applicable). “Pass” means within that tolerance, not numerical identity. For example, the major moment reference is 3.197482 kN-m, while the continuous extremum is 3.198388 kN-m. Symmetric members and combination ties can give different governing identifiers.

The full physical response is separate from the legacy reference definition:

| Physical quantity | Value |
|---|---:|
| Maximum absolute MZ | 3.198388053 kN-m |
| Maximum absolute FY | 6.843225961 kN |
| Maximum absolute FX | 2.281542034 kN |
| Absolute resultant displacement | 11.868198629 mm |
| Member chord-relative resultant displacement | 5.729748573 mm |

Largest normalized force balance residual: 1.21e-16. Largest normalized moment balance residual: 2.47e-17. These assess numerical equilibrium, not the physical adequacy of the engineering model.

## Sample 7 load path

The user confirmed the support policy: pinned supports carry gravity and wind; the other supports carry wind only. The original file already follows that policy. Its eight PINNED nodes (7, 11, 19, 23, 31, 35, 43, 47) restrain all translations. The other twelve supports restrain global X and Z only.

However, nodes 1–5, 13–17, 25–29 and 37–41 form a lower assembly with no vertical restraint. The local axial FX releases at the starts of members 5, 16, 27 and 38 disconnect this assembly's vertical force path from the pinned supports above. All 20 lower nodes can translate together vertically without member strain.

The test `tests/test_sample7_stability.py` constructs this rigid displacement mode directly. Its normalized stiffness residual is approximately 8.66e-19, while gravity has nonzero virtual work of -11994.59676816 N per metre of that displacement. Therefore the original linear model has no static equilibrium solution under DL. Wind-only supports cannot carry this gravity load.

The solver reports `SINGULAR_MATRIX` with the affected nodes, returns no analysis result, and keeps undeformed geometry available for review after failed-job file cleanup. It does not insert artificial springs, suppress the mode or use the supplied output as a result.

**Required model clarification:** can the four start joints physically transmit axial force, or is a separate pinned support missing from the lower assembly? Replacing `FX MY MZ` with `MY MZ` at those four joints would change the load path and requires confirmation of the actual connection. No such change has been applied to the attached model.

## Solver additions

`MEMBER LOAD ... CMOM X/Y/Z/GX/GY/GZ magnitude station` now supports concentrated bending and torsional couples in force × length units. Load vectors use cross-section rotation functions, including shear flexibility, coordinate transformations and release condensation. Recovery includes moment/torsion jumps, rotations and displacements; envelopes evaluate both interior sides of couples and only the inward limit at member endpoints. Coincident point forces and moments remain distinct through combinations.

Pure-couple equilibrium uses a dimensionally consistent force scale M/L and moment scale F×L when the corresponding resultant is zero. The relative tolerance remains unchanged. This prevents roundoff-only force residuals from being treated as a physical imbalance under pure moment loading.

Independent tests cover cantilever closed forms in all three axes, member-end couples, both bending planes with/without shear, six local/global directions, released connections, equivalent split-member models with nodal moments, force/moment coexistence, unit conversion, combinations, interior jump extrema and load scales from 1e-20 to 1e20. Full test and HTTP evidence is summarized in `BUILD_VERIFICATION.md`.

## Overall status

Across the five analyzable models with supplied references, **156 of 164 evaluated entries pass**, with the same eight documented differences from Samples 1–3. Sample 5 has no supplied reference. Sample 7's 40 reference entries are blocked by its unstable original model. All individual entries remain visible in `validation/comparison.json` and `VALIDATION_RESULTS.md`.

These checks do not prove universal accuracy, certification or superiority to STAAD.Pro. No licensed live OpenSTAAD run was performed.
