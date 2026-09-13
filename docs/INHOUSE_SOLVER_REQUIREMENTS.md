# Requirements from the actual archive

Implement linear elastic spatial frames with six degrees of freedom per node, isotropic E/Poisson/density, full PRIS AX/IX/IY/IZ and optional shear areas, BETA orientation, PINNED/FIXED/FIXED BUT supports, and local end releases including axial sliding connections. Normalize every statement at its active UNIT to N, m and Pa. Density is weight per volume, not mass density.

Parse semicolon records, continued lines, TO ranges, multiple UNIT statements, SELFWEIGHT, concentrated member forces, rectangular two-way FLOOR LOAD and primary/linear combination cases. Add nodal forces, uniform member loading and fixed supports for independent mathematical benchmarks. Reject unknown stiffness/loading commands explicitly with source line context.

Recover all six member end forces, in-span equilibrium functions, bending extrema including discontinuities, nodal displacements, loaded-member absolute translations and chord-relative deflection. Do not use cubic endpoint interpolation alone for a loaded beam. Preserve global and grouped result semantics by reusing original envelope/classification code.

Validation must include analytical beams/frames, load equilibrium, releases, local-axis transformations, combination superposition, mechanism rejection, parser errors, original API/classifier tests and quantitative reference comparison. Report absent reference coverage and discrepancies. The API must run without Windows COM/Bentley, support abort and timeout, and retain optional OpenSTAAD operation.
