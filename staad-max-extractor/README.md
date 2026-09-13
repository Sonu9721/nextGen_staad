# STAAD Max Extractor

This module is a sibling of `python_module/vertical-fin` and is meant for
OpenSTAAD automation from Python.

## What it does

1. Connects to STAAD.Pro using `win32com.client.Dispatch("StaadPro.OpenSTAAD")`
2. Opens an existing `.std` file
3. Runs analysis
4. Extracts:
   - maximum absolute member end bending moment
   - maximum absolute nodal displacement
5. Prints JSON output, including the governing bending moment and maximum displacement

## Profile-specific extraction (Fully Unitized)

When `extraction_flow=fully_unitized` (API form field or `ExtractionConfig.extraction_flow`), the extractor:

1. Parses `MEMBER PROPERTY AMERICAN` PRIS lines and classifies members into `mullion`, `head`, `sill`, `transom`, and `stack`
2. Computes per-profile BM/SF/AF/DF envelopes by reusing the same OpenSTAAD extraction functions with filtered member sets
3. Returns the standard global `properties` block plus `properties.global`, per-profile blocks, and `profiles.member_counts`

The default `standard` flow keeps the legacy global-only response unchanged.

Classification logic lives in [`profile_classifier.py`](profile_classifier.py).

## Casement extraction

When `extraction_flow=casement`:

1. Runs the same global envelope as `standard` (unchanged `properties` shape; absolute governing magnitudes)
2. Classifies members into `interlock`, `central_meeting`, `fixed_mullion`, `horizontal`, and `outer` via [`casement_classifier.py`](casement_classifier.py)
3. Appends a top-level `casement` object with **signed** BM/SF/AF/DF `max`/`min` per profile ([`casement_extractor.py`](casement_extractor.py))

The `casement` object is **not** present for `standard` or `fully_unitized`. Classification uses optional `generation_request.casement_profiles` / `casementProfiles` when supplied; otherwise geometry (outer frame at bounding X/Y, interior horizontals, transom-split verticals, median-column central meeting). Duplicate member ids are assigned to the first matching profile; unknown ids are ignored.

Signed envelopes are algebraic, not absolute: `max` is the most positive sample and `min` is the most negative. Missing samples stay `null`. Displacement (`df`) prefers member-chord intermediate deflection for vertical profiles so shared junction motion does not dominate; exclusive nodal RESULTANT is still included, and `horizontal` also includes absolute nodal RESULTANT at shared junctions.

If Casement extraction fails after analysis, the extractor still returns the global `properties` block plus an empty `casement` object.

```bash
python app.py --file "model.std" --loadcase all --include-combinations --moment-mode internal-envelope --extraction-flow casement --pretty
```

Example `casement` object (excerpt). The other four profiles use the same `member_ids` + `bm`/`sf`/`af`/`df` shape:

```json
{
  "casement": {
    "extraction_flow": "casement",
    "profiles": {
      "interlock": {
        "member_ids": [22, 26],
        "bm": {
          "unit": "kN-m",
          "max": { "value": 0.743, "member_id": 22, "axis": "MZ", "load_case": 3 },
          "min": { "value": -0.51, "member_id": 26, "axis": "MZ", "load_case": 4 }
        },
        "sf": {
          "unit": "kN",
          "max": { "value": 1.2, "member_id": 22, "axis": "FY", "load_case": 3 },
          "min": { "value": -0.8, "member_id": 26, "axis": "FY", "load_case": 4 }
        },
        "af": {
          "unit": "kN",
          "max": { "value": 0.4, "member_id": 22, "axis": "FX", "load_case": 3 },
          "min": { "value": -1.1, "member_id": 26, "axis": "FX", "load_case": 4 }
        },
        "df": {
          "unit": "mm",
          "max": { "value": 12.4, "member_id": 22, "direction": "RESULTANT", "load_case": 3 },
          "min": { "value": 0.0, "member_id": 26, "direction": "RESULTANT", "load_case": 1 }
        }
      },
      "central_meeting": { "member_ids": [24] },
      "fixed_mullion": { "member_ids": [21, 23, 25] },
      "horizontal": { "member_ids": [47, 48, 49, 50] },
      "outer": { "member_ids": [1, 2] }
    }
  }
}
```

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
python app.py --file "C:\Users\HP\Desktop\FSS\excel_reports\testing\staad_pro_integration\sample_std\Facade_STAAD_Report_1.std" --loadcase 3 --pretty
```

Envelope across all load cases:

```bash
python app.py --file "C:\path\to\model.std" --loadcase all --pretty
```

By default, bending moments come from member end forces only. To inspect the
full-member internal moment envelope instead, add:

```bash
python app.py --file "C:\path\to\model.std" --loadcase all --moment-mode internal-envelope --pretty
```

If you also want load combinations included in the envelope, add:

```bash
python app.py --file "C:\path\to\model.std" --loadcase all --include-combinations --pretty
```

To save logs to file while still printing them on console:

```bash
python app.py --file "C:\path\to\model.std" --loadcase all --verbose --log-file ".\logs\extractor-debug.log"
```

Use `--log-append` to append instead of overwrite.

Default output units:

- bending moment: `kN-m`
- displacement: `mm`

By default, displacement is reported as STAAD's `Resultant` magnitude so it lines up
with the annotation workflow used in post-processing. Optional axis switches:

```bash
python app.py --file "C:\path\to\model.std" --moment-axis My --displacement-axis Y --pretty
```

For member-span displacement, the extractor now prefers STAAD's absolute section
displacement results so beam annotations can govern over nodal values when appropriate.

If the script cannot detect STAAD model units from the `.std` file, pass them explicitly:

```bash
python app.py --file "C:\path\to\model.std" --source-force-unit KN --source-length-unit METER --pretty
```

## Example JSON

```json
{
  "file": "C:\\path\\to\\model.std",
  "analysis": {
    "load_case_mode": "all",
    "load_cases": [1, 3, 4],
    "moment_mode": "internal-envelope",
    "include_combinations": false
  },
  "units": {
    "output": {
      "force": "kN",
      "length": "mm",
      "moment": "kN-m"
    },
    "staad_detected": {
      "force": "KN",
      "length": "METER",
      "moment": "KN-meter"
    },
    "file_detected": {
      "force": "KN",
      "length": "METER",
      "moment": "KN-meter"
    }
  },
  "properties": {
    "bending_moment": {
      "unit": "kN-m",
      "major": {
        "axis": "MZ",
        "value": 0.37,
        "member_id": 2,
        "location": "end",
        "load_case": 4
      },
      "minor": {
        "axis": "MY",
        "max": {
          "value": 0.4,
          "member_id": 2,
          "location": "end",
          "load_case": 3
        },
        "at_major_governing_point": {
          "value": 0.08,
          "member_id": 2,
          "location": "end",
          "load_case": 4,
          "reference_major_value": 0.37,
          "reference_major_load_case": 4
        }
      }
    },
    "shear_force": {
      "unit": "kN",
      "major": {
        "axis": "FY",
        "value": 2.1,
        "member_id": 2,
        "member_type": "mullion",
        "location": "start",
        "station": 0.0,
        "load_case": 3,
        "governing_end": "start"
      },
      "minor": {
        "axis": "FZ",
        "max_on_mullions": {
          "value": 2.7,
          "member_id": 4,
          "member_type": "mullion",
          "location": "end",
          "station": 1.0,
          "load_case": 4,
          "governing_end": "end"
        },
        "at_major_governing_point": {
          "value": 1.4,
          "member_id": 2,
          "member_type": "mullion",
          "location": "start",
          "station": 0.0,
          "governing_load_case": 5,
          "reference_major_load_case": 3,
          "reference_major_value": 2.1
        }
      }
    },
    "axial_force": {
      "unit": "kN",
      "axis": "FX",
      "value": 1.2,
      "member_id": 3,
      "location": "end",
      "load_case": 4
    },
    "displacement": {
      "unit": "mm",
      "direction": "RESULTANT",
      "value": 12.3,
      "node_id": 105,
      "source": "node",
      "load_case": 3
    }
  }
}
```

## Notes

- This script is designed for a Windows machine with STAAD.Pro installed.
- Extracted engineering values are grouped under `properties` by result category.
  Legacy scattered result keys are not emitted by the normal payload.
- Casement (`--extraction-flow casement`) is additive: the global `properties`
  block stays standard-shaped, and signed profile envelopes appear only under
  the top-level `casement` object.
- OpenSTAAD method signatures vary a little between STAAD versions, so the code
  tries a few common call patterns before failing.
- Validation against a real STAAD installation is still required because the
  sandbox here cannot launch COM automation.
