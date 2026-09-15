# Fully unitized: measured before and after

Both reference envelopes were evaluated against the unchanged STD files. Original baselines are retained in validation/usg-baseline. The standard acceptance thresholds are unchanged. A stricter investigation threshold (max 0.01%, 0.00002 kN/kN m or 0.001 mm) additionally exposes small differences; it is not a substitute for design qualification.

The fixed defect is classification by PRIS row order. Canonical geometry and released splice levels now assign roles, and a complete explicit profile_member_ids mapping can override that convention. This preserves supplied-model results and supports reordered/custom property assignments.

Moment values at reference stations agree within a few millionths of kN m; continuous peak stations differ. Displacement discrepancies persist at the reference point. Alternative shear coefficients and moment-area reconstructions were investigated but did not establish reference equivalence. No empirical adjustment was made.

| Model / metric | Reference | Before | After | Absolute error | Relative error | Strict status |
|---|---:|---:|---:|---:|---:|---|
| USG_1 / bending_moment.major | 3.539295 | 3.547253 | 3.547253 | 0.007958000 | 0.224847% | difference |
| USG_1 / bending_moment.minor.max | 0.11173 | 0.11173 | 0.11173 | 0.000000000 | 0.000000% | pass |
| USG_1 / bending_moment.minor.at_major_governing_point | 0.0 | 0.0 | 0.0 | 0.000000000 | N/A | pass |
| USG_1 / shear_force.major | 3.796476 | 3.796479 | 3.796479 | 0.000003000 | 0.000079% | pass |
| USG_1 / shear_force.minor.max_on_mullions | 0.005675 | 0.005675 | 0.005675 | 0.000000000 | 0.000000% | pass |
| USG_1 / shear_force.minor.at_major_governing_point | 1e-06 | 1e-06 | 1e-06 | 0.000000000 | 0.000000% | pass |
| USG_1 / axial_force | 2.332851 | 2.332851 | 2.332851 | 0.000000000 | 0.000000% | pass |
| USG_1 / displacement | 31.987816 | 32.030124 | 32.030124 | 0.042308000 | 0.132263% | difference |
| USG_1 / mullion.bending_moment.major | 3.539295 | 3.547253 | 3.547253 | 0.007958000 | 0.224847% | difference |
| USG_1 / mullion.bending_moment.minor.max | 0.006739 | 0.006739 | 0.006739 | 0.000000000 | 0.000000% | pass |
| USG_1 / mullion.bending_moment.minor.at_major_governing_point | 0.0 | 0.0 | 0.0 | 0.000000000 | N/A | pass |
| USG_1 / mullion.shear_force.major | 3.796476 | 3.796479 | 3.796479 | 0.000003000 | 0.000079% | pass |
| USG_1 / mullion.shear_force.minor.max_on_mullions | 0.005675 | 0.005675 | 0.005675 | 0.000000000 | 0.000000% | pass |
| USG_1 / mullion.shear_force.minor.at_major_governing_point | 1e-06 | 1e-06 | 1e-06 | 0.000000000 | 0.000000% | pass |
| USG_1 / mullion.axial_force | 2.332851 | 2.332851 | 2.332851 | 0.000000000 | 0.000000% | pass |
| USG_1 / mullion.displacement | 31.987816 | 32.030124 | 32.030124 | 0.042308000 | 0.132263% | difference |
| USG_1 / stack.bending_moment.major | 0.1728 | 0.1728 | 0.1728 | 0.000000000 | 0.000000% | pass |
| USG_1 / stack.shear_force.major | 0.432 | 0.432 | 0.432 | 0.000000000 | 0.000000% | pass |
| USG_1 / stack.displacement | 2.509251 | 2.509179 | 2.509179 | 0.000072000 | 0.002869% | pass |
| USG_1 / head.bending_moment.major | 0.0864 | 0.0864 | 0.0864 | 0.000000000 | 0.000000% | pass |
| USG_1 / head.shear_force.major | 0.216 | 0.216 | 0.216 | 0.000000000 | 0.000000% | pass |
| USG_1 / head.displacement | 2.982192 | 2.982089 | 2.982089 | 0.000103000 | 0.003454% | pass |
| USG_1 / sill.bending_moment.major | 0.064856 | 0.064756 | 0.064756 | 0.000100000 | 0.154188% | difference |
| USG_1 / sill.shear_force.major | 0.270047 | 0.269964 | 0.269964 | 0.000083000 | 0.030735% | difference |
| USG_1 / sill.displacement | 0.395347 | 0.39903 | 0.39903 | 0.003683000 | 0.931587% | difference |
| USG_1 / transom.bending_moment.major | 0.1728 | 0.1728 | 0.1728 | 0.000000000 | 0.000000% | pass |
| USG_1 / transom.shear_force.major | 0.432 | 0.432 | 0.432 | 0.000000000 | 0.000000% | pass |
| USG_1 / transom.displacement | 0.39644 | 0.398179 | 0.398179 | 0.001739000 | 0.438654% | difference |
| USG_2 / bending_moment.major | 10.042384 | 10.043964 | 10.043964 | 0.001580000 | 0.015733% | difference |
| USG_2 / bending_moment.minor.max | 0.134771 | 0.13477 | 0.13477 | 0.000001000 | 0.000742% | pass |
| USG_2 / bending_moment.minor.at_major_governing_point | 0.0 | 0.0 | 0.0 | 0.000000000 | N/A | pass |
| USG_2 / shear_force.major | 7.86267 | 7.86267 | 7.86267 | 0.000000000 | 0.000000% | pass |
| USG_2 / shear_force.minor.max_on_mullions | 0.020404 | 0.020404 | 0.020404 | 0.000000000 | 0.000000% | pass |
| USG_2 / shear_force.minor.at_major_governing_point | 2e-06 | 2e-06 | 2e-06 | 0.000000000 | 0.000000% | pass |
| USG_2 / axial_force | 2.558435 | 2.558435 | 2.558435 | 0.000000000 | 0.000000% | pass |
| USG_2 / displacement | 165.864593 | 166.009154 | 166.009154 | 0.144561000 | 0.087156% | difference |
| USG_2 / mullion.bending_moment.major | 10.042384 | 10.043964 | 10.043964 | 0.001580000 | 0.015733% | difference |
| USG_2 / mullion.bending_moment.minor.max | 0.024225 | 0.024225 | 0.024225 | 0.000000000 | 0.000000% | pass |
| USG_2 / mullion.bending_moment.minor.at_major_governing_point | 0.0 | 0.0 | 0.0 | 0.000000000 | N/A | pass |
| USG_2 / mullion.shear_force.major | 7.86267 | 7.86267 | 7.86267 | 0.000000000 | 0.000000% | pass |
| USG_2 / mullion.shear_force.minor.max_on_mullions | 0.020404 | 0.020404 | 0.020404 | 0.000000000 | 0.000000% | pass |
| USG_2 / mullion.shear_force.minor.at_major_governing_point | 2e-06 | 2e-06 | 2e-06 | 0.000000000 | 0.000000% | pass |
| USG_2 / mullion.axial_force | 2.558435 | 2.558435 | 2.558435 | 0.000000000 | 0.000000% | pass |
| USG_2 / mullion.displacement | 165.864593 | 166.009154 | 166.009154 | 0.144561000 | 0.087156% | difference |
| USG_2 / stack.bending_moment.major | 0.32955 | 0.32955 | 0.32955 | 0.000000000 | 0.000000% | pass |
| USG_2 / stack.shear_force.major | 0.7605 | 0.7605 | 0.7605 | 0.000000000 | 0.000000% | pass |
| USG_2 / stack.displacement | 4.905264 | 4.905086 | 4.905086 | 0.000178000 | 0.003629% | pass |
| USG_2 / head.bending_moment.major | 0.152625 | 0.152625 | 0.152625 | 0.000000000 | 0.000000% | pass |
| USG_2 / head.shear_force.major | 0.36 | 0.36 | 0.36 | 0.000000000 | 0.000000% | pass |
| USG_2 / head.displacement | 12.253715 | 12.253554 | 12.253554 | 0.000161000 | 0.001314% | pass |
| USG_2 / sill.bending_moment.major | 0.123239 | 0.123049 | 0.123049 | 0.000190000 | 0.154172% | difference |
| USG_2 / sill.shear_force.major | 0.475049 | 0.474903 | 0.474903 | 0.000146000 | 0.030734% | difference |
| USG_2 / sill.displacement | 0.261322 | 0.266525 | 0.266525 | 0.005203000 | 1.991030% | difference |
| USG_2 / transom.bending_moment.major | 0.32955 | 0.32955 | 0.32955 | 0.000000000 | 0.000000% | pass |
| USG_2 / transom.shear_force.major | 0.7605 | 0.7605 | 0.7605 | 0.000000000 | 0.000000% | pass |
| USG_2 / transom.displacement | 0.534239 | 0.536581 | 0.536581 | 0.002342000 | 0.438381% | difference |

## Governing members, stations and cases

All station values below are normalized to metres. N/A is retained when the source does not supply a station; endpoint labels are not converted into lengths. Member/case differences at tied envelopes are distinct from value differences.

| Model / metric | Ref member / node | Actual member / node | Ref station m | Actual station m | Ref / actual case | Location status |
|---|---|---|---:|---:|---|---|
| USG_1 / bending_moment.major | member 17 | member 17 | 0.814998319 | 0.920110000 | 2 / 2 | reference_station_reproduced_true_extremum_differs |
| USG_1 / bending_moment.minor.max | member 40 | member 40 | 0.599998851 | 0.600000000 | 1 / 1 | equivalent_within_tolerance |
| USG_1 / bending_moment.minor.at_major_governing_point | member 17 | member 17 | 0.814998319 | 0.920110000 | 2 / 2 | equivalent_within_tolerance |
| USG_1 / shear_force.major | member 15 | member 15 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_1 / shear_force.minor.max_on_mullions | member 1 | member 1 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_1 / shear_force.minor.at_major_governing_point | member 15 | member 15 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_1 / axial_force | member 11 | member 11 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_1 / displacement | member 17 | member 17 | 0.814998319 | 0.815000000 | 3 / 3 | equivalent_within_tolerance |
| USG_1 / mullion.bending_moment.major | member 17 | member 17 | 0.814998319 | 0.920110000 | 2 / 2 | reference_station_reproduced_true_extremum_differs |
| USG_1 / mullion.bending_moment.minor.max | member 1 | member 1 | 0.000000000 | 0.000000000 | 1 / 1 | equivalent_within_tolerance |
| USG_1 / mullion.bending_moment.minor.at_major_governing_point | member 17 | member 17 | 0.814998319 | 0.920110000 | 2 / 2 | equivalent_within_tolerance |
| USG_1 / mullion.shear_force.major | member 15 | member 15 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_1 / mullion.shear_force.minor.max_on_mullions | member 1 | member 1 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_1 / mullion.shear_force.minor.at_major_governing_point | member 15 | member 15 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_1 / mullion.axial_force | member 11 | member 11 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_1 / mullion.displacement | member 17 | member 17 | 0.814998319 | 0.815000000 | 3 / 3 | equivalent_within_tolerance |
| USG_1 / stack.bending_moment.major | member 45 | member 50 | 0.599998648 | 0.600000000 | 2 / 2 | equivalent_within_tolerance |
| USG_1 / stack.shear_force.major | member 45 | member 50 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_1 / stack.displacement | node 24 | node 14 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_1 / head.bending_moment.major | member 55 | member 57 | 0.599998851 | 0.600000000 | 2 / 2 | equivalent_within_tolerance |
| USG_1 / head.shear_force.major | member 55 | member 57 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_1 / head.displacement | node 20 | node 20 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_1 / sill.bending_moment.major | member 37 | member 39 | 1.199997676 | 0.000000000 | 2 / 2 | equivalent_within_tolerance |
| USG_1 / sill.shear_force.major | member 37 | member 39 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_1 / sill.displacement | member 37 | member 39 | 0.499999025 | 0.700000000 | 3 / 3 | equivalent_within_tolerance |
| USG_1 / transom.bending_moment.major | member 42 | member 42 | 0.599998648 | 0.600000000 | 2 / 2 | equivalent_within_tolerance |
| USG_1 / transom.shear_force.major | member 42 | member 40 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_1 / transom.displacement | member 53 | member 53 | 0.599998851 | 0.600000000 | 3 / 3 | equivalent_within_tolerance |
| USG_2 / bending_moment.major | member 18 | member 18 | 1.583330311 | 1.620038000 | 2 / 2 | reference_station_reproduced_true_extremum_differs |
| USG_2 / bending_moment.minor.max | member 58 | member 58 | 0.649998700 | 0.650000000 | 1 / 1 | equivalent_within_tolerance |
| USG_2 / bending_moment.minor.at_major_governing_point | member 18 | member 18 | 1.583330311 | 1.620038000 | 2 / 2 | equivalent_within_tolerance |
| USG_2 / shear_force.major | member 16 | member 16 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_2 / shear_force.minor.max_on_mullions | member 1 | member 1 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_2 / shear_force.minor.at_major_governing_point | member 16 | member 16 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_2 / axial_force | member 19 | member 19 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_2 / displacement | member 18 | member 18 | 1.583330361 | 1.583333000 | 3 / 3 | equivalent_within_tolerance |
| USG_2 / mullion.bending_moment.major | member 18 | member 18 | 1.583330311 | 1.620038000 | 2 / 2 | reference_station_reproduced_true_extremum_differs |
| USG_2 / mullion.bending_moment.minor.max | member 1 | member 1 | 0.000000000 | 0.000000000 | 1 / 1 | equivalent_within_tolerance |
| USG_2 / mullion.bending_moment.minor.at_major_governing_point | member 18 | member 18 | 1.583330311 | 1.620038000 | 2 / 2 | equivalent_within_tolerance |
| USG_2 / mullion.shear_force.major | member 16 | member 16 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_2 / mullion.shear_force.minor.max_on_mullions | member 1 | member 1 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_2 / mullion.shear_force.minor.at_major_governing_point | member 16 | member 16 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_2 / mullion.axial_force | member 19 | member 19 | N/A | N/A | 1 / 1 | equivalent_within_tolerance |
| USG_2 / mullion.displacement | member 18 | member 18 | 1.583330361 | 1.583333000 | 3 / 3 | equivalent_within_tolerance |
| USG_2 / stack.bending_moment.major | member 47 | member 49 | 0.649998700 | 0.650000000 | 2 / 2 | equivalent_within_tolerance |
| USG_2 / stack.shear_force.major | member 47 | member 48 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_2 / stack.displacement | node 15 | node 15 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_2 / head.bending_moment.major | member 62 | member 63 | 0.649998700 | 0.650000000 | 2 / 2 | equivalent_within_tolerance |
| USG_2 / head.shear_force.major | member 62 | member 64 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_2 / head.displacement | node 22 | node 33 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_2 / sill.bending_moment.major | member 41 | member 41 | 1.299997400 | 1.300000000 | 2 / 2 | equivalent_within_tolerance |
| USG_2 / sill.shear_force.major | member 41 | member 41 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_2 / sill.displacement | member 41 | member 43 | 0.541665592 | 0.758333000 | 3 / 3 | equivalent_within_tolerance |
| USG_2 / transom.bending_moment.major | member 44 | member 45 | 0.649998700 | 0.650000000 | 2 / 2 | equivalent_within_tolerance |
| USG_2 / transom.shear_force.major | member 44 | member 56 | N/A | N/A | 2 / 2 | equivalent_within_tolerance |
| USG_2 / transom.displacement | member 57 | member 57 | 0.649998700 | 0.650000000 | 3 / 3 | equivalent_within_tolerance |

The 13 diagnostic stations are for inspection only; physical extrema are solved continuously. Metadata comparisons, station conversions to metres and evaluation at the reference point are retained in comparison.json. Governing IDs can differ at tied members/cases; value comparison and metadata equality are reported separately.
