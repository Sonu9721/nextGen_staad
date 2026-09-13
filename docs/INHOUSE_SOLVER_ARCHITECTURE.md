# In-house solver architecture

Parser -> canonical model -> validation/units -> load generation -> element assembly -> solve -> force/displacement recovery -> original envelopes/classifiers -> job API.

Internal units: N, m, Pa, N-m. Output: kN, mm, kN-m. In-house stations: metres. The legacy staad_detected field reports N/METER for the numerical adapter.

## Element and solution

Each prismatic spatial frame element has 12 DOFs: axial extension, Saint-Venant torsion and Timoshenko bending in both planes. Euler-Bernoulli is the zero-shear-flexibility limit. Explicit AY/AZ take precedence. With both YD and ZD but no shear areas, the engineering assumption is Cowper's rectangular correction 10(1+nu)/(12+11nu) times supplied AX, not the solid bounding area of a hollow section. This is not a universal extrusion formula. The factor is independently described in [Kennedy et al.](https://public.websites.umich.edu/~mdolaboratory/pdf/Kennedy2011a.pdf).

Local x follows incidence. Nonvertical local z is normalized x cross global Y; local y completes the frame. Upward vertical members have y=-global X and z=global Z. Positive BETA rotates y toward z about x. Releases condense both stiffness and equivalent nodal loading. Released element-end rotations/translations are recovered after solution.

The global symmetric matrix is diagonally scaled, checked for positive eigenvalues, and solved once for multiple primary right-hand sides. Combinations superpose results without reassembly. Only unloaded, disconnected rotations may be omitted. Translational or coupled mechanisms fail. Residuals, load balance, conditioning and member endpoint compatibility are checked/reported.

Version 0.2 detects disconnected DOFs relative to their original element stiffness, avoiding an absolute cutoff that misclassified uniformly scaled materials. The acceptance check uses the normwise backward error of the scaled system for each primary case. An unregularized componentwise diagnostic is also exported; near-zero components can have a large relative ratio despite a negligible absolute residual. Physical equilibrium is independently computed from original point/distributed loads and actual support reactions, about a translated origin, for every primary and combination case.

## Loads and recovery

Four-point Gaussian integration of exact homogeneous Timoshenko shape functions produces consistent equivalent nodal loads. Loads remain piecewise linear or concentrated at exact stations. Floor generation traces directed planar faces, retains collinear subdivisions, and assigns triangular/trapezoidal 45-degree tributaries. Intersections must be connected; faces must be complete axis-aligned rectangles. Total transferred load is checked against pressure times area.

Internal forces follow equilibrium integrals. Shear polynomial roots and all discontinuities locate true moment extrema. Raw translations integrate flexure, shear and axial strain, then verify endpoint compatibility. Absolute and chord-relative modes remain separate. The JSON adapter uses Hermite endpoint interpolation and an Euler-Bernoulli fixed-end load bubble for section translations, reflecting the reference's flexural section recovery limitation. Remaining differences are documented.

The additive `physical_response` report uses normalized interval polynomials for all six local force components and the squared resultant translation. Derivative roots and interval endpoints locate continuous extrema; both limits at a concentrated-force jump are included. Full shear deformation is retained in both absolute and chord-relative translations. The original `properties` and `casement` fields keep their established reporting definitions. The interface defaults to physical response and offers an explicitly labeled legacy view. Profile tables continue to use legacy definitions.

## Integration

Numerical geometry/output adapters feed the original _extract_property_envelope, _build_grouped_result_payload, unitized classifier and casement classifier/extractor. No COM attachment or global result monkeypatch is used. Analysis metadata includes diagnostics and an explicit validation status. The original job API and result wrapper remain. In-house work runs in a dedicated spawned process with cancellation checks and a bounded termination fallback. No Bentley process is touched.

## Public sources

* Bentley: [floor load to triangular/trapezoidal member loads](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0112271).
* Bentley: [orientation and BETA](https://bentleysystems.service-now.com/community?id=kb_article&sysparm_article=KB0113346).
* Bentley: [joint versus section shear-deformation behavior](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0112641).
* Kennedy et al.: [Timoshenko theory and Cowper shear correction](https://public.websites.umich.edu/~mdolaboratory/pdf/Kennedy2011a.pdf).

Only public mathematical methods and user-supplied integration code were used. No proprietary solver code was copied or decompiled.
