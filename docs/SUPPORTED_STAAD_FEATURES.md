# Supported STAAD subset

This is a linear elastic frame solver, not a complete implementation of STAAD.Pro.

| Feature | Support |
|---|---|
| STAAD SPACE | Six degrees of freedom per node |
| JOINT COORDINATES / MEMBER INCIDENCES | Explicit nodes, two-node members, semicolon records |
| UNIT | Per-statement conversion; m, mm/MMS, cm, inch, foot and common force units |
| Continuations / ranges | Trailing hyphens, ALL and ascending TO selections |
| Isotropic material | E, POISSON, weight DENSITY; ALPHA/DAMP and TYPE/STRENGTH accepted as metadata |
| PRIS | Explicit positive AX/IX/IY/IZ; optional AY/AZ/YD/ZD |
| BETA | Right-handed rotation about incidence-oriented local x |
| Supports | PINNED, FIXED, FIXED BUT with free global DOFs |
| Releases | Local START/END FX/FY/FZ/MX/MY/MZ with condensation; incompatible sets rejected |
| JOINT LOAD | Global forces and moments |
| SELFWEIGHT | Global X/Y/Z for all members using weight density |
| MEMBER LOAD | CON force and station; CMOM concentrated moment and station; full/partial UNI force; X/Y/Z or GX/GY/GZ. CMOM uses force × length units and cross-section rotation shape functions. |
| FLOOR LOAD | Uniform pressure, explicit GX/GY/GZ, complete axis-aligned rectangular panels, split boundaries, two-way 45-degree tributaries |
| Cases | Primary cases and linear combinations of primary cases |
| Analysis | One PERFORM ANALYSIS, then FINISH |
| Metadata | Job information, INPUT WIDTH, full-line asterisk comments, PRINT after analysis |

Unsupported commands fail explicitly. Exclusions include distributed member moments (UMOM), load offsets, section tables, shells, member offsets, truss/tension-only members, nonlinear/P-Delta/buckling/dynamic analysis, temperature, settlement, springs, inclined supports, design checks, repeat/generation commands, partial-panel pressures, nonrectangular panels and unconnected panel intersections.

The dense solver is bounded to 1000 nodes and 2000 members. Supplied models contain 39-70 nodes and 54-105 members. Conditioning and residuals appear under analysis.diagnostics.
