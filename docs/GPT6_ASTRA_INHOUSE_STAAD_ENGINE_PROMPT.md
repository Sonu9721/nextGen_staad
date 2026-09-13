# GPT-6 Astra Advanced — Build an In-House STAAD-Type Structural Analysis Engine

## How to use this prompt

Paste this entire document into **GPT-6 Astra Advanced** as the system/task brief.

Then attach, in the same conversation:

1. This repository (or at least the files listed in §0).
2. One or more **sample `.std` files** (NextGen-generated facade models).
3. The matching **reference result JSON** from the current STAAD.Pro/OpenSTAAD extractor.

Do **not** start modifying production code until Phase 1 analysis is complete.

If sample files are attached after the first analysis response, **refine the plan against those files before implementation**.

---

## Recommended AI Model

**Use: GPT-6 Astra Advanced**

This task requires deep repository analysis, structural-analysis reasoning, numerical-method design, reverse engineering of an existing STAAD/OpenSTAAD *integration* (not STAAD source), and substantial backend implementation.

Do **not** start modifying code immediately. First understand the existing architecture and establish a technically defensible implementation strategy.

---

# 0. Repository reconnaissance already performed (verify, then extend)

The following was extracted from the current `staad-report-extractor` repository. Treat it as **CONFIRMED FROM REPOSITORY** unless you find a contradiction while inspecting the code. Mark any correction as such.

This repository is **not** NextGen itself. It is the **STAAD extraction API** that NextGen calls. NextGen generates the `.std` elsewhere and uploads it here. Do not search this repo for STAAD *model generation* of the facade; it is not here.

## 0.1 What this repository actually is

Python FastAPI service:

```text
Client (NextGen)
   ↓
POST /jobs  (multipart .std upload + optional generation_request.json)
   ↓
In-memory JobManager queue
   ↓
Background worker (one STAAD job at a time by default)
   ↓
Spawned child process → staad-max-extractor/extractor.py
   ↓
STAAD.Pro + OpenSTAAD COM / openstaadpy
   ↓
PERFORM ANALYSIS (inside STAAD)
   ↓
OpenSTAAD result queries
   ↓
Envelope + facade classification
   ↓
JSON result
   ↓
GET /jobs/{job_id}/result
```

**CONFIRMED:** There is **no** `POST /analyze` endpoint. The live contract is the job API below.

## 0.2 Repository layout (files that matter)

| Path | Role |
| --- | --- |
| `app/main.py` | FastAPI routes: `/health`, `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/result`, `POST /jobs/{id}/abort` |
| `app/job_manager.py` | In-memory jobs, queue, worker loop, progress, abort, cleanup |
| `app/schemas.py` | Pydantic job/health/error models |
| `app/core/config.py` | Env settings (`STAAD_*`, timeouts, retries, lock, combinations flag) |
| `app/services/staad_runner.py` | Spawns extractor child process; retries; STAAD host lock; abort/timeout kill |
| `app/services/staad_process.py` | Kill STAAD process trees (`Bentley.Staad.exe`, `STAADPro.exe`, `SProStaad.exe`) |
| `app/services/health_checks.py` | `/health?check=license` (Bentley) and `/health?check=staad` (OpenSTAAD attach) |
| `app/services/job_cleanup.py` | Stale job-folder pruning |
| `docs/openapi.yaml` | Published API contract |
| `README.md` | Human API/result documentation (authoritative for payload meaning) |
| `staad-max-extractor/extractor.py` | **All OpenSTAAD analysis + result extraction** (~3800 lines) |
| `staad-max-extractor/profile_classifier.py` | Fully Unitized member classification (PRIS order + geometry) |
| `staad-max-extractor/casement_classifier.py` | Casement member classification (request map or geometry) |
| `staad-max-extractor/casement_extractor.py` | Casement **signed** BM/SF/AF/DF envelopes |
| `staad-max-extractor/tests/fixtures/Casement_CF-3T4S-3F-C_STAAD.std` | Real casement `.std` fixture in-repo |
| `staad-max-extractor/tests/` | Parser/classifier/envelope unit tests (OpenSTAAD mocked; **no live solver tests**) |
| `tests/test_api.py` | API/job-manager tests with extractor mocked |

## 0.3 Current public API (must remain compatible)

### Endpoints

- `GET /health` — `check=server|license|staad`
- `POST /jobs` — `202` with `{ "job_id", "status": "queued" }`
- `GET /jobs/{job_id}` — progress polling
- `GET /jobs/{job_id}/result` — extraction payload when `succeeded`
- `POST /jobs/{job_id}/abort` — cancel queued or force-stop running job

### `POST /jobs` form fields

- `std_file` (required `.std`)
- `timeout_seconds` (default 180)
- `max_retries` (default 2)
- `retry_delay_seconds` (default 20)
- `staadpro_path` (legacy; **accepted but unused** by OpenSTAAD extractor)
- `extraction_flow`: `standard` (default) | `fully_unitized` | `casement`  
  Aliases: `unitized` / `fullyunitized` → `fully_unitized`; hyphenated `casement` → `casement`
- `generation_request` optional JSON file:
  - Fully Unitized: `panels[].segments` geometry + optional flow override
  - Casement: `casement_profiles` / `casementProfiles` member-id map + flow override
  - Flow override keys: `flowType`, `flow_type`, `extractionFlow`, `extraction_flow`

### Job status values

`queued` | `running` | `succeeded` | `failed` | `canceled`

Progress fields: `progress_percent` (0–100; non-terminal capped at 99), `message`, `result_ready`.

### Hardcoded extractor settings from the API worker

From `app/services/staad_runner.py` `_run_extraction_child`:

```text
load_case            = "all"
moment_axis          = "MZ"
moment_mode          = "internal-envelope"
include_combinations = Settings.extractor_include_combinations  (default True)
displacement_axis    = "RESULTANT"
moment_output_unit   = "kN-m"
displacement_output_unit = "mm"
```

The in-house engine must reproduce **this** production path first, not the CLI’s default `end-forces` mode.

## 0.4 What is delegated to STAAD.Pro / OpenSTAAD today

**Everything structurally meaningful after topology parse.**

The existing `.std` parser in `parse_std_file_metadata()` only extracts:

- Joint coordinates and member incidences
- Units at `PERFORM ANALYSIS` (for converting OpenSTAAD numbers)
- Primary load-case IDs vs combination IDs and `TITLE` names

It does **not** parse materials, sections, supports, releases, BETA, selfweight, member loads, floor loads, or combinations factors. STAAD.Pro consumes the file and does analysis. OpenSTAAD then returns forces/displacements.

### OpenSTAAD methods actually used (replace these with the in-house solver)

**Launch / file / analysis**

- `StaadPro.OpenSTAAD` COM / Bentley `openstaadpy`
- `OpenSTAADFile` / `OpenSTAADFile2` / `OpenFile`
- `AnalyzeEx(1, 1, 1)` (openstaadpy) or `PerformAnalysis` / `Analyze` / `RunAnalysis`
- `AreResultsAvailable` / `IsPostProcessingReady` / `IsAnalyzing`

**Geometry (often falls back to `.std` parse)**

- `GetMemberCount` / `GetMemberList` / `GetBeamList`
- `GetNodeCount` / `GetNodeList`
- `GetMemberIncidence` / `GetBeamLength`
- `GetNodeCoordinates`

**Results (this is the oracle the new engine must replace)**

- `GetMemberEndForces(member, end, load_case, local_flag=0)` → 6-vector  
  `[FX, FY, FZ, MX, MY, MZ]` in **local** axes (`MEMBER_FORCE_LOCAL_FLAG = 0` is empirically required)
- `GetMinMaxBendingMoment(member, axis, load_case)` →  
  `[min_value, min_station, max_value, max_station]`
- `GetIntermediateMemberForcesAtDistance(member, distance, load_case)`
- `GetNodeDisplacements(node, load_case)` → `[UX, UY, UZ, …]`
- `GetIntermediateMemberAbsTransDisplacements(member, distance, load_case)`
- `GetIntermediateDeflectionAtDistance(member, distance, load_case)` (relative-to-chord deflection)
- `GetPrimaryLoadCaseNumbers()`
- `GetBaseUnit()` → ENGLISH → convert as KIP/IN; METRIC → KN/METER

**Not used / explicitly skipped**

- Post-processing UI mode switch (`GoToPostProcessingMode`) — skipped because of modal dialogs
- STAAD listing/ANL text parsing — replaced already by OpenSTAAD
- `staadpro_path` — leftover from an older CLI workflow

## 0.5 Result contract (production JSON)

Succeeded `GET /jobs/{id}/result`:

```json
{
  "job_id": "...",
  "status": "succeeded",
  "duration_seconds": 138.36,
  "working_directory": "...",
  "cleanup_performed": true,
  "attempts": 1,
  "max_attempts": 3,
  "result": { }
}
```

`result` always contains `file`, `analysis`, `units`, `properties`. Additional keys depend on flow.

### Standard / Sliding (`extraction_flow=standard`)

`properties` is a **global absolute-magnitude envelope**:

| Quantity | Axis | How obtained | Member filter |
| --- | --- | --- | --- |
| `bending_moment.major` | `MZ` | `GetMinMaxBendingMoment` (internal-envelope) | all members |
| `bending_moment.minor.max` | `MY` | same, independent envelope | all members |
| `bending_moment.minor.at_major_governing_point` | `MY` | force at the **same member/station/load case** as governing MZ | that point |
| `shear_force.major` | `FY` | **member end forces only** (not internal envelope) | all members |
| `shear_force.minor.max_on_mullions` | `FZ` | end forces | **vertical members only** (`member_type=mullion`) |
| `shear_force.minor.at_major_governing_point` | `FZ` | at the **fixed member+end** where FY governs, **max abs FZ across all selected load cases** (load case may differ from FY’s) | that point |
| `axial_force` | `FX` | member end forces | **inner verticals only** (`inner_mullion`); empty `{}` if none |
| `displacement` | `RESULTANT` | max of nodal resultant **and** member-span sampled absolute displacement | all nodes/members |

Governing rule for standard/unitized global envelopes: **maximum absolute value** after conversion to output units. Values stored in JSON are **unsigned magnitudes** (`abs(converted)`), except Casement signed envelopes.

Displacement resultant:

```text
sqrt(UX² + UY² + UZ²)
```

Member-span displacement samples **11 interior stations** (`MEMBER_DEFLECTION_SAMPLE_POINTS = 11`), excluding ends (nodes already cover ends). Prefers absolute section displacements; falls back to intermediate deflection.

For Fully Unitized **transom** displacement only: uses member-span deflection minus the smaller of the two support-node displacements (relative transom deflection). Envelope across load cases is selected by the **global** span max, then the relative value is reported.

### Fully Unitized (`extraction_flow=fully_unitized`)

Keeps global `properties.{bending_moment,shear_force,axial_force,displacement}` **and** adds:

- `properties.global` — mirror of the global envelope
- `properties.mullion` / `stack` / `head` / `sill` / `transom` — omitted if empty
- `profiles.extraction_flow = "fully_unitized"`
- `profiles.member_counts`

Mullion profile blocks include minor MY / FZ. Horizontal profile groups include **major-axis values only** (`include_minor_axes=False`).

Classification source of truth (`profile_classifier.py`):

1. Parse `MEMBER PROPERTY AMERICAN` PRIS lines in **emission order**.
2. Map line order to `mullion, stack, head, sill, transom` (stack only if ≥5 PRIS lines).
3. **Override:** any geometrically vertical member → `mullion`.
4. Ambiguous identical PRIS fingerprints for horizontals → classify by Y vs panel geometry (sill at Y=0, head at assembly top, stack at intermediate panel tops, else transom).
5. Panel geometry from `generation_request.panels[].segments`, or inferred from already-tagged stack member Y.

Do **not** hardcode member-id ranges.

### Casement (`extraction_flow=casement`)

Global `properties` stays **standard-shaped** (absolute). Adds top-level `result.casement`:

- Profiles always present: `interlock`, `central_meeting`, `fixed_mullion`, `horizontal`, `outer`
- Each has `member_ids` + signed `bm`/`sf`/`af`/`df` with `max` (most positive) and `min` (most negative)
- `bm` from internal MZ envelope (signed)
- `sf` from FY end forces (signed)
- `af` from FX end forces (signed)
- `df` prefers member-chord **relative** deflection; exclusive nodal RESULTANT included; `horizontal` also includes shared-junction nodal RESULTANT
- Classification: request map if present, else geometry (outer at bounding min/max X and Y; interior horizontals; below-transom interior verticals → `fixed_mullion`; above-transom median X column → `central_meeting`; remaining → `interlock`)
- Soft-fail: if Casement extraction throws after analysis, job still succeeds with empty casement object
- Fixture expected geometry (`Casement_CF-3T4S-3F-C_STAAD.std`):  
  interlock `[22,26]`, central_meeting `[24]`, fixed_mullion `[21,23,25]`, horizontal `[47,48,49,50]`, outer `1–20, 27–46, 51–70`

### Units block (always)

```json
"units": {
  "output": { "force": "kN", "length": "mm", "moment": "kN-m" },
  "staad_detected": { "force": "KIP|KN|...", "length": "IN|METER|...", "moment": "..." },
  "file_detected": { "force": "...", "length": "...", "moment": "..." }
}
```

`staad_detected` is OpenSTAAD runtime base unit (often ENGLISH/KIP-IN even when the file is metric). `file_detected` is the UNIT statement active at `PERFORM ANALYSIS`.

Existing conversion tables: `FORCE_TO_KN` and `LENGTH_TO_M` in `extractor.py`. Reuse the same factors.

## 0.6 `.std` feature inventory from the in-repo casement fixture

File: `staad-max-extractor/tests/fixtures/Casement_CF-3T4S-3F-C_STAAD.std`

**CONFIRMED commands actually present:**

```text
STAAD SPACE
START JOB INFORMATION / ENGINEER DATE / END JOB INFORMATION
INPUT WIDTH 79
UNIT <length> <force>          # many times; order of tokens varies
JOINT COORDINATES              # 3D nodes, meters in this file
MEMBER INCIDENCES              # 2-node beams
DEFINE MATERIAL START
  ISOTROPIC ALUMINUM / CONCRETE / STEEL
  E, POISSON, DENSITY, ALPHA, DAMP
  TYPE STEEL / STRENGTH ...    # unused by extractor; needed only if solver uses E, ν, ρ
END DEFINE MATERIAL
CONSTANTS
  BETA 90 ALL                  # local-axis rotation — CRITICAL for MZ/FY meaning
  MATERIAL ALUMINUM ALL
MEMBER PROPERTY AMERICAN
  <ranges> PRIS AX .. IX .. IY .. IZ .. YD .. ZD ..
  line continuation with trailing '-'
SUPPORTS
  <node ranges> PINNED
MEMBER RELEASE
  <members> START MY MZ
  <members> END MY MZ
LOAD n LOADTYPE None TITLE DL|WL
  SELFWEIGHT Y -1
  MEMBER LOAD
    <member> CON GY <force> <distance>
  FLOOR LOAD
    ZRANGE ... FLOAD ... XRANGE ... YRANGE ... GZ
LOAD COMB n TITLE
  <case> <factor> <case> <factor>
PERFORM ANALYSIS
FINISH
```

**CONFIRMED structural interpretation of this fixture (engineering, still REQUIRES VALIDATION against STAAD):**

- 3D space frame (`STAAD SPACE`), all Z=0 → planar facade in XY, wind in ±Z.
- Prismatic beam properties, not AISC table sections.
- Supports: `PINNED` → restrain UX,UY,UZ; free RX,RY,RZ.
- Releases: MY and MZ hinges at specified member starts/ends (axial/torsion/shear still transferred unless also released — they are not).
- `BETA 90 ALL` rotates local Y/Z 90° about member local X. This is why the application treats **MZ as major bending** and **FY as major shear**. Reproduce STAAD’s BETA convention; do not guess.
- Dead load: self-weight in global −Y plus concentrated `CON GY` member loads at given distances (distance in **active UNIT length** at that statement).
- Wind: `FLOOR LOAD` pressure `FLOAD -3` in `GZ` over a huge box (`±100`). This is **STAAD’s area-to-member load generator**, not a nodal load list. Implementing STAAD-compatible FLOOR LOAD is a first-class requirement for matching this model.
- Combinations: `3 = 1.0*DL + 1.0*WL`, `4 = 1.0*DL + (-1.0)*WL`.
- Units **change throughout the file**. Geometry, E, PRIS, supports, and loads are each in the UNIT active at that line. An in-house parser **must** convert at parse time. The current extractor does not, because STAAD does it.

**Not present in this fixture — do not implement initially:**

```text
plates / shells / solids
REPEAT, COPY, MIRROR
table sections (W12x26, pipes, etc.)
MEMBER OFFSET / REFZT / REFYT
truss / tension-only / cable
P-DELTA / nonlinear / buckling / dynamics
temperature / settlement / time history
PRINT / design PARAMETER / CODE CHECK
fixed supports, springs, inclined supports (unless a later sample needs them)
JOINT LOAD (unless a later sample needs them)
UDL UNI / LIN member loads (unless a later sample needs them)
```

When a later sample uses a new command: add it deliberately, with tests. Fail clearly on unsupported commands that affect stiffness or loading.

## 0.7 Inner-mullion / vertical classification (standard axial & minor shear)

Independent of Fully Unitized PRIS groups:

- Vertical member: `|ΔY| > 0` and `|ΔY| ≥ max(|ΔX|, |ΔZ|)`.
- Minor FZ envelope: vertical members only (`mullion`).
- Axial FX envelope: verticals **not** on the bounding min/max X or min/max Z of the vertical set (`inner_mullion`). If every vertical is peripheral, axial result is `{}`.

## 0.8 Job / abort / process semantics to preserve

- Jobs live in memory (`JobManager.jobs`); folders under `STAAD_JOBS_DIR/job_{id}/`.
- One host-level STAAD lock unless `ALLOW_PARALLEL_STAAD=true`.
- Extractor runs in a **spawned child process**. Abort kills that PID tree plus STAAD images.
- After success: trim generated artifacts; keep `.std` (and `.anl` only if `KEEP_ANL_FILE`).
- Cancel/error: may delete the job folder (`KEEP_JOB_FOLDER_ON_ERROR`).
- Retries only on transient STAAD attach/timeout/lock failures, not invalid input.
- Progress stages today are STAAD-centric (“Connecting to STAAD.Pro”, “Running STAAD analysis”). For the in-house engine, keep the same percent bands where practical, but change messages to parser/assemble/solve/recover.

## 0.9 What should remain unchanged vs what to replace

**Keep unchanged (preferred):**

- FastAPI routes and JSON job envelope
- Extraction-flow names and result-shape rules
- `profile_classifier.py` / `casement_classifier.py` (reuse; they are already independent of OpenSTAAD)
- Casement signed-envelope *semantics* in `casement_extractor.py` (replace only the OpenSTAAD getters with solver recovery)
- Unit conversion tables and output units
- Tests that assert payload shape and classification

**Replace:**

- `extractor.run()` analysis + OpenSTAAD result queries
- `staad_runner` child process that *must* launch STAAD
- License/STAAD health checks as **hard production dependencies** (keep as optional oracle)

**Preferred integration:** add an analysis backend switch, e.g. `ANALYSIS_BACKEND=inhouse|openstaad`, defaulting to `inhouse` once validated, with OpenSTAAD retained as a reference oracle for comparison tests.

## 0.10 Risks already visible

1. **FLOOR LOAD** must match STAAD’s member-load distribution or headline wind results will be wrong.
2. **BETA 90 ALL** controls which local axis is “major”. Wrong BETA → MZ/FY swap.
3. **UNIT switching** in the file: PRIS in cm, loads in m, materials in N/cm, etc.
4. **Member releases** at transom/mullion connections — ignoring them over-stiffens the frame.
5. **Internal moment envelope vs end forces:** production uses internal envelope for BM, end forces for shear/axial. Do not unify these incorrectly.
6. **OpenSTAAD English base units** vs metric file units: reference JSON `staad_detected` may be KIP/IN even when `file_detected` is KN/METER. In-house engine should set `staad_detected` to the **solver internal/output conversion source**; document the mapping. Do not fake KIP/IN if the solver never used them. Prefer: `staad_detected` = internal solver units (recommend SI N, m) and still emit `file_detected` from the `.std`.
7. **Relative transom DF** and **Casement chord deflection** are application-specific, not generic FE output.
8. No live STAAD in CI — current tests mock COM. New solver tests must be analytical + fixture-based.

---

# 1. Objective

Our NextGen application currently depends on this repository API, which accepts a STAAD `.std` file and uses **STAAD.Pro + OpenSTAAD API** to execute/extract structural-analysis results.

Develop an **in-house STAAD-Pro-type structural analysis engine** so the application can perform the required analysis and result extraction **without depending on STAAD.Pro/OpenSTAAD** for supported facade models.

The objective is **NOT to clone all of STAAD.Pro**.

The objective is to implement the **minimum but technically robust structural-analysis capability required by NextGen Facade**, while preserving compatibility with the existing API and result contract.

Sample `.std` file(s) and corresponding reference result JSON will be provided separately as reference material. Also use the in-repo casement fixture.

---

# 2. Critical Instruction — Analyze Before Coding

Before making any code changes:

1. Thoroughly inspect the entire current repository (verify §0).
2. Understand:

   * Existing API architecture
   * STAAD file ingestion
   * `.std` parsing (today: topology/units/load-case IDs only)
   * Model representation
   * Node/member generation (**not in this repo** — NextGen)
   * Section/profile handling
   * Material properties
   * Load generation
   * Load cases / combinations
   * Boundary/support conditions
   * Member releases
   * Analysis execution (today: STAAD)
   * Current OpenSTAAD integration
   * Result extraction and envelope semantics
   * Unit conversion
   * Error handling
   * Job lifecycle
   * `/abort` / process termination
   * Existing tests
   * Existing frontend/backend expectations
3. Identify exactly which functionality is currently delegated to STAAD.Pro/OpenSTAAD.
4. Trace the complete data flow:

```text
NextGen UI                          (external app, not this repo)
   ↓
STAAD model generation              (external; produces .std)
   ↓
.std file uploaded to this API
   ↓
Current STAAD extraction API        (this repo)
   ↓
STAAD.Pro / OpenSTAAD
   ↓
Analysis
   ↓
Result extraction + facade envelopes
   ↓
Result JSON
   ↓
NextGen calculations / UI           (external)
```

5. Identify what the in-house engine must replace.
6. Identify what should remain unchanged.

**Do not redesign the entire application unnecessarily.**

Preferred architecture: replace the STAAD/OpenSTAAD-dependent analysis/extraction layer while maintaining the existing job API contract.

---

# 3. Current External Result Contract

See §0.5 for the exact production semantics.

The API returns a structure similar to:

```json
{
  "job_id": "...",
  "status": "succeeded",
  "duration_seconds": 138.36,
  "working_directory": "...",
  "cleanup_performed": true,
  "attempts": 1,
  "max_attempts": 3,
  "result": {
    "file": "...std",
    "analysis": {
      "load_case_mode": "all",
      "load_cases": [1, 2, 3, 4],
      "moment_mode": "internal-envelope",
      "include_combinations": true
    },
    "units": {
      "output": { "force": "kN", "length": "mm", "moment": "kN-m" },
      "staad_detected": { "force": "KIP", "length": "IN", "moment": "KIP-in" },
      "file_detected": { "force": "KN", "length": "METER", "moment": "KN-meter" }
    },
    "properties": {
      "bending_moment": {},
      "shear_force": {},
      "axial_force": {},
      "displacement": {}
    }
  }
}
```

The detailed supplied sample may contain:

* Major/minor bending moment
* Governing member / station / load case
* Major/minor shear (including mullion-only FZ)
* Axial force (inner verticals)
* Displacement (node or member-span)
* Global + mullion/stack/head/sill/transom blocks
* Profile member counts
* Units and conversions
* Optional top-level `casement` object

Current axis conventions in the application:

```text
major bending  = MZ
minor bending  = MY
major shear    = FY
minor shear    = FZ
axial          = FX
displacement   = RESULTANT, mm
```

---

# 4. Primary Engineering Goal

Determine the smallest structural-analysis engine capable of correctly supporting the structural behavior required by this application.

**INFERRED FROM CURRENT BEHAVIOR + FIXTURE (REQUIRES VALIDATION):**

A **3D linear elastic static frame analysis using the Direct Stiffness Method** is sufficient:

- 2-node 3D beam/frame elements (12 DOF)
- Axial + bending about both local axes + torsion + optional shear deformation (Timoshenko if needed to match STAAD; start with Euler-Bernoulli, add shear deformation if reference discrepancy is consistent with shear flexibility)
- Prismatic sections (`AX, IX, IY, IZ`)
- Isotropic linear materials (`E`, `POISSON` → `G = E/2(1+ν)`)
- Pinned supports, member end moment releases
- Nodal equivalent loads from self-weight, concentrated member loads, and FLOOR LOAD
- Multiple primary cases + linear combinations
- No dynamics, no P-delta, no plates in the known models

Do not assume this blindly if a supplied sample contains plates, nonlinear commands, or other element types.

## Structural model (required)

* 3D nodes
* Beam/frame members
* Member connectivity
* Member orientation / BETA / local axes
* Member length
* Section properties (PRIS)
* Material properties
* Boundary conditions (`PINNED` first)
* End releases (`MY`, `MZ` first)
* Load application (self-weight, CON member loads, FLOOR LOAD)
* Load cases and combinations

## Structural analysis (required)

* Axial deformation
* Bending about both local axes
* Torsion (at least in the element formulation; MX may not be reported in current JSON)
* 3D frame behavior
* Member loads → equivalent nodal loads
* Support reactions (needed for recovery even if not in JSON)
* Member end forces in local axes
* Member internal forces along the member
* Nodal and span displacement
* Result envelopes matching existing semantics

---

# 5. STAAD `.std` Compatibility

Analyze the actual `.std` files used by this project (fixture + user-supplied samples).

Do not implement every STAAD command.

1. Extract the actual command subset (start from §0.6; extend from samples).
2. Create a formal supported-command list (`SUPPORTED_STAAD_FEATURES.md`).
3. Implement a parser for that subset, **including UNIT-aware conversion**.
4. Fail clearly for unsupported commands that would change results.
5. Ignore or skip commands that cannot affect stiffness/loading (`START JOB INFORMATION`, `INPUT WIDTH`, `PRINT`, comments `*`).
6. Support STAAD line continuation (`-` at end of line) and `TO` ranges.

### Parser requirements that the current code does **not** do

The new parser must, at each statement, apply the **currently active UNIT**:

```text
UNIT METER KN     → nodes in m, later loads in kN and m
UNIT CM NEWTON    → E, density in those units
UNIT CM KN        → PRIS AX cm², I cm⁴, YD/ZD cm
UNIT MMS KN       → support block (coordinates already stored)
UNIT METER KN     → SELFWEIGHT / MEMBER LOAD / FLOOR LOAD / COMB
```

Preferred: convert immediately into internal SI (N, m, Pa) and never mix file units in the solver.

---

# 6. Model Representation

Design an internal canonical structural model independent of STAAD syntax.

```text
StructuralModel
 ├── Nodes
 ├── Members
 ├── Materials
 ├── Sections
 ├── Supports
 ├── Releases
 ├── LoadCases
 ├── LoadCombinations
 └── AnalysisSettings
```

Each member must contain enough information to build its 12×12 local stiffness matrix:

```text
memberId, startNode, endNode, material, section,
orientation/BETA, length, localAxis, releases, memberType?
```

Do not tightly couple the solver to `.std` parsing.

Preferred pipeline:

```text
STAAD Parser
      ↓
Canonical Structural Model
      ↓
Validation
      ↓
Unit Normalization (if not already SI at parse)
      ↓
Load / Boundary Condition Builder
      ↓
Finite Element / Matrix Solver
      ↓
Result Recovery (end forces, internal stations, displacements)
      ↓
Envelope (absolute or signed, per existing flow)
      ↓
Facade Result Classifier (reuse existing classifiers)
      ↓
Application Result Adapter (existing JSON)
```

---

# 7. Finite Element / Matrix Solver

If repository analysis confirms a linear 3D frame solver is sufficient, implement a proper matrix structural-analysis solver.

Typical 3D beam/frame element DOFs:

```text
UX UY UZ RX RY RZ   × 2 nodes  =  12 DOF
```

Implement:

* Local stiffness matrix (axial, torsion, bending Y, bending Z; shear deformation optional)
* Transformation matrix using member axis + BETA
* Global stiffness assembly
* Equivalent nodal loads from member loads (in global, consistent with transformation)
* Boundary conditions + releases (release via modified element stiffness or condensation — document the choice)
* Solve `K U = F`
* Recover local end forces: `q_local = k_local u_local - p_fixed_end`
* Recover internal forces at stations from end forces + in-span loads (beam equilibrium). **Do not** take governing BM only from ends when CON/UDL/FLOOR LOAD exist.

Do not use simplified determinate-beam formulas as the global solution. Facade frames are statically indeterminate.

Preserve:

* Member orientation
* Local/global transformations
* STAAD-compatible sign conventions **as far as needed to match OpenSTAAD local FX/FY/FZ/MX/MY/MZ**
* Load transformations
* End-force recovery

**ENGINEERING ASSUMPTION:** Match OpenSTAAD local-force sign convention empirically against the reference JSON and, if needed, a tiny two-member verification model run through the existing extractor (oracle), not by copying STAAD internals.

---

# 8. Numerical Stability

The solver must detect:

* Singular stiffness matrix / mechanism / unrestrained DOF
* Zero-length members
* Invalid section or material properties
* Duplicate or unreferenced nodes
* Invalid member references
* Excessive conditioning problems

Return meaningful engineering errors. Do not silently produce NaN, Inf, or `0` for a failed analysis.

Example:

```json
{
  "status": "failed",
  "error": {
    "code": "SINGULAR_STRUCTURE",
    "message": "The structural model contains insufficient restraints."
  }
}
```

Map into existing `ApiError` (`error_code`, `message`, `retryable=false`, `stage=analysis`) so `GET /jobs/{id}` and `/result` stay compatible.

---

# 9. Loads and Load Cases

Analyze how existing `.std` files generate loads. Do not invent a generic load system first.

From the casement fixture (extend from user samples):

| Source | STAAD | Direction | Notes |
| --- | --- | --- | --- |
| Dead | `SELFWEIGHT Y -1` | global Y | mass from `DENSITY * AX * L`; factor −1 |
| Dead | `MEMBER LOAD … CON GY F d` | global Y | concentrated force at distance `d` from **start** joint (STAAD convention — **REQUIRES VALIDATION**) |
| Wind | `FLOOR LOAD … FLOAD p … GZ` | global Z | pressure → member loads on beams in the floor plane |
| Comb 3 | `1 1.0 2 1.0` | | DL+WL |
| Comb 4 | `1 1.0 2 -1.0` | | DL−WL |

Primary titles: `DL`, `WL`.

Implement only what actual files need. FLOOR LOAD is not optional if samples use it.

Document FLOOR LOAD algorithm explicitly (which members receive load, tributary width, one-way vs two-way). If exact STAAD FLOOR LOAD replication is uncertain, mark **REQUIRES VALIDATION** and compare member end forces against the oracle on a minimal floor-load model.

---

# 10. Load Combination / Envelope Logic

Production:

```text
load_case_mode = all
moment_mode = internal-envelope
include_combinations = true
```

Therefore envelopes run over **primary cases and combinations**.

Do **not** simply take max absolute nodal displacement and stop.

Reproduce:

* Per-load-case results, then envelope
* Combination results as linear superposition of primary cases (same `K`, different `F`; combinations should **not** reassemble K)
* Member-end vs internal-station distinction per quantity (see §0.5)
* Absolute max for standard/unitized; signed max/min for casement
* Governing case, member, station/end
* Companion MY at governing MZ point
* Companion FZ at governing FY **point**, enveloped across load cases

The engine must reproduce engineering meaning, not merely JSON field names.

---

# 11. Internal Member Forces

For each member, recover internal forces along the member.

Need extraction at:

```text
start, end, internal stations
```

Production BM uses `GetMinMaxBendingMoment` (true min/max along member, not just samples). Prefer:

1. Recover end forces.
2. Using in-span loads, evaluate BM/SF as functions of x.
3. Find analytical extrema between load discontinuities (CON loads, FLOOR-distributed segments).
4. Also evaluate at ends.

Sampling 0%, 10%, … 100% is acceptable only if extrema from load discontinuities are also included. Do not miss a concentrated-load kink.

Quantities:

* MZ, MY — internal envelope (production)
* FY, FZ, FX — currently **ends only** in production JSON (except companion point lookup which may be an interior station if BM governed interior). Keep this split unless samples prove OpenSTAAD used interior shear.
* MX/torsion — formulate the element; report only if the contract needs it (currently not in grouped payload)

---

# 12. Displacement Recovery

Production displacement:

* Nodal `RESULTANT`
* Member-span absolute translation resultant at interior stations
* Report whichever magnitude is larger (except transom relative mode)
* Can originate from `source: "node"` or `source: "member-span"`

Example:

```json
{
  "value": 10.278243,
  "load_case": 2,
  "direction": "RESULTANT",
  "source": "node",
  "node_id": 27
}
```

Casement DF uses **relative chord deflection** for members (displacement relative to the straight line between current displaced ends), not the same as global absolute displacement.

Implement both recovery modes; select per flow as existing code does.

Do not assume the maximum always occurs at a node.

---

# 13. Unit System

Current application output:

```text
force = kN
length = mm
moment = kN-m
```

Source models use mixed STAAD UNITs.

Implement a **centralized** unit-conversion layer. Do not scatter conversions through the solver.

Preferred:

```text
Input Units (per STAAD statement)
     ↓
Normalize to Internal SI (N, m, Pa, N·m)
     ↓
Solver
     ↓
Result Recovery (SI)
     ↓
Convert to Application Units (kN, mm, kN-m)
```

Document the internal solver unit system in `INHOUSE_SOLVER_ARCHITECTURE.md`.

Reuse `FORCE_TO_KN` / `LENGTH_TO_M` factors already in `extractor.py` for output conversion.

Stations in JSON today are often left in **source length unit** (`station_unit`: `"meter"` or `"in"` depending on runtime units). Match existing behavior: station distances in the same length unit as the solver/source used for OpenSTAAD (`file` SI meters if in-house SI), and set `station_unit` accordingly. If you change station units, it will break consumers — prefer meters for in-house and document it; only use `"in"` if mimicking OpenSTAAD English runtime.

---

# 14. Result Contract

Expose the same result contract so NextGen does not need unnecessary changes.

```text
SolverResult
      ↓
Existing envelope builders (_extract_property_envelope / casement_extractor)
      ↓
_build_grouped_result_payload
      ↓
Existing JSON
```

Populate `properties`, `global`, `mullion`, `stack`, `head`, `sill`, `transom`, `profiles`, `casement`, `units`, `analysis` where applicable.

Example major BM:

```json
{
  "bending_moment": {
    "unit": "kN-m",
    "major": {
      "value": 4.286399,
      "member_id": 48,
      "location": "member-minimum",
      "load_case": 2,
      "station": 31.023561,
      "station_unit": "in",
      "axis": "MZ"
    }
  }
}
```

Exact semantics come from current implementation + samples, not assumptions.

---

# 15. Facade-Specific Member Classification

Do not embed fragile assumptions such as `member_id 1–27 = mullion`.

Reuse:

* `staad-max-extractor/profile_classifier.py`
* `staad-max-extractor/casement_classifier.py`
* `_get_inner_vertical_member_ids` / `_is_vertical_member` logic from `extractor.py`

Keep the solver generic:

```text
Generic Structural Solver
          ↓
Generic Member/Node Results
          ↓
Existing Facade Classifiers
          ↓
Existing Envelope / Result Adapter
```

---

# 16. Validation Strategy

The first implementation must be validated against the existing STAAD/OpenSTAAD engine.

For every supplied `.std`:

```text
Same .std
   ↓
Current STAAD/OpenSTAAD          (oracle; Windows + licensed STAAD)
   ↓
Reference Result JSON

Same .std
   ↓
New In-House Solver
   ↓
Candidate Result
```

Build an automated comparison framework.

Compare at minimum:

### Global

* Maximum displacement
* Major / minor bending
* Major / minor shear
* Axial force

### Member-level (where reference allows)

* End forces
* Internal governing BM station and load case

### Profile-level

* Mullion / stack / head / sill / transom
* Casement: interlock / central_meeting / fixed_mullion / horizontal / outer

If OpenSTAAD is not available in the implementation environment, still:

1. Unit-test the solver against analytical Level-1 models.
2. Compare against **user-supplied reference JSON**.
3. Leave an oracle comparison test that skips when STAAD is absent.

---

# 17. Numerical Tolerance

Do not require exact floating-point equality.

Define configurable engineering tolerances (absolute + relative).

Justify from: method, units, conditioning, oracle behavior, design precision.

Suggested starting point (**ENGINEERING ASSUMPTION**, tune after first comparison):

```text
displacement:  rel 1e-3 or abs 0.05 mm
force:         rel 1e-3 or abs 0.01 kN
moment:        rel 1e-3 or abs 0.01 kN-m
```

Governing IDs (member_id, node_id, load_case) should match when values are not near-ties. If two locations are within tolerance, document as a tie, not a failure.

Create a comparison report:

```text
Metric                  Reference       In-house       Difference
------------------------------------------------------------------
Max displacement        ...             ...            ...
Major BM                ...             ...            ...
Major shear             ...             ...            ...
Axial force             ...             ...            ...
```

Do not declare the solver correct merely because a few headline numbers match.

---

# 18. Test Models

## Level 1 — Mathematical benchmark

Manually verifiable:

* Simply supported beam (UDL and mid-span point load)
* Cantilever
* Fixed-fixed beam
* Axially loaded bar
* Beam with end moment release
* 2D portal frame
* Small 3D frame with BETA rotation
* Pinned-support mechanism check (must fail with SINGULAR_STRUCTURE)

## Level 2 — STAAD parser tests

Nodes, members, PRIS, materials, BETA, supports, releases, UNIT switching, SELFWEIGHT, CON loads, FLOOR LOAD, LOAD COMB, unsupported-command errors, line continuation.

## Level 3 — Solver tests

Compare known analytical solutions in SI.

## Level 4 — STAAD reference tests

Compare against OpenSTAAD JSON when oracle is available.

## Level 5 — Real NextGen models

User-supplied facade `.std` + in-repo casement fixture.

---

# 19. Do Not Make This Mistake

Do NOT create a fake implementation that:

* Hardcodes sample values or member IDs
* Returns approximate values only to match the sample
* Uses formulas that work only for one facade configuration
* Ignores load combinations
* Ignores local axes / BETA
* Ignores member releases
* Ignores distributed / floor / concentrated load in-span effects
* Treats every member as a simple determinate beam
* Assumes all structures are statically determinate
* Generates plausible-looking but structurally incorrect results

The engine must be a real structural-analysis implementation.

---

# 20. Copyright / Clean-Room Consideration

Implement our own engine.

* Do not copy proprietary STAAD.Pro / OpenSTAAD source.
* Do not decompile proprietary binaries.
* Do not reproduce proprietary implementation internals.

Use:

* Publicly known structural-analysis mathematics (direct stiffness / matrix structural analysis)
* Our own parser, model, solver, and recovery
* OpenSTAAD only as a **reference behavior / validation oracle**

---

# 21. Performance

Models in this domain are small (tens to low hundreds of members). Dense 12N×12N is acceptable initially.

Profile later: parse, assemble, factorize, recover, memory.

Do not prematurely optimize. First establish correctness.

Investigate sparse solvers only if model sizes justify them.

Do not introduce heavy numerical dependencies (`scipy` sparse, etc.) without demonstrating need. NumPy is reasonable if added to `requirements.txt`.

---

# 22. API Architecture

Keep the existing job API.

Ideal in-house path:

```text
POST /jobs
       ↓
queue / worker / abort (unchanged)
       ↓
Parse STD → StructuralModel
       ↓
Run in-house analysis (cancellation-aware)
       ↓
Recover results
       ↓
Existing envelope + classifier + JSON adapter
       ↓
GET /jobs/{id}/result
```

Do not break: `job_id`, `status`, retries, cleanup, abort, existing consumers.

Recommended config:

```text
ANALYSIS_BACKEND=inhouse|openstaad
```

When `inhouse`:

* Do not require pywin32/STAAD for the happy path.
* `/health?check=staad` can report `inhouse` readiness (parser/solver import) rather than COM attach.
* `/health?check=license` can remain for oracle mode only.

---

# 23. Cancellation / Abort

Preserve `POST /jobs/{id}/abort`.

In-house path should **not** need to kill `Bentley.Staad.exe`. Instead:

* Queued: mark canceled (already implemented).
* Running: set `abort_requested`; solver checks a cancellation callback between parse / assemble / factorize / load-case loop / recovery.

Cancellation must not leave locked resources, corrupt temp files, or inconsistent job status.

Long operations:

```text
Parse → Assemble → Factorize K → For each load case solve/recover → Envelope
```

must periodically check cancellation.

---

# 24. Error Handling

Structured errors, mapped into existing `ApiError.error_code` where possible.

New codes (also acceptable to namespace under existing `extractor_failed` with `stage`):

```text
INVALID_STD_FILE
UNSUPPORTED_STAAD_COMMAND
INVALID_NODE
INVALID_MEMBER
INVALID_SECTION
INVALID_MATERIAL
INVALID_LOAD
INVALID_SUPPORT
ZERO_LENGTH_MEMBER
SINGULAR_MATRIX
MECHANISM_DETECTED
NUMERICAL_FAILURE
UNSUPPORTED_ANALYSIS_FEATURE
ANALYSIS_CANCELLED
RESULT_RECOVERY_FAILED
```

Distinguish:

```text
input/model error     → retryable false, stage input_validation
analysis error        → retryable false, stage analysis
system error          → retryable maybe, stage execution
cancellation          → error_code canceled (already used)
```

---

# 25. Implementation Phases

Do not implement everything in one uncontrolled change.

## Phase 1 — Repository reconnaissance

Produce `CURRENT_ARCHITECTURE.md` (can refine this prompt’s §0). **No production behavior change.**

## Phase 2 — Requirements extraction

Produce `INHOUSE_SOLVER_REQUIREMENTS.md` from this repo + supplied samples.

## Phase 3 — Solver architecture

Produce `INHOUSE_SOLVER_ARCHITECTURE.md`:

```text
Parser → Canonical Model → Validation → Unit Normalization
 → Load Assembly → Stiffness Assembly → Boundary Conditions
 → Matrix Solve → Member Force Recovery → Displacement Recovery
 → Envelope → Facade Classification → Result Adapter
```

## Phase 4 — Minimal vertical slice

Smallest end-to-end path on a **hand-verified** beam/frame, then JSON.

Do not start with the most complex facade model.

## Phase 5 — Real facade model

Run supplied `.std` + in-repo casement fixture. Compare to reference JSON. Document every discrepancy.

## Phase 6 — Expand feature coverage

Only when required by actual models.

---

# 26. Required Deliverables

### Documentation

```text
docs/CURRENT_ARCHITECTURE.md
docs/INHOUSE_SOLVER_REQUIREMENTS.md
docs/INHOUSE_SOLVER_ARCHITECTURE.md
docs/SUPPORTED_STAAD_FEATURES.md
docs/VALIDATION_RESULTS.md
docs/KNOWN_LIMITATIONS.md
```

### Code (suggested package)

Keep FastAPI in `app/`. Add a modular engine, for example:

```text
engine/
  parser/          # .std subset, UNIT-aware
  model/           # canonical structural model
  units/
  loads/           # selfweight, CON, FLOOR LOAD, combinations
  solver/          # DSM 3D frame
  recovery/        # end forces, internals, displacements
  envelope/        # or reuse extractor envelope functions
  adapter/         # SolverResult → existing getters used by payload builder
```

Separation required:

```text
parser, model, units, loads, solver,
result recovery, envelope, facade classification, API adapter
```

Reuse classification modules rather than rewriting them.

### Tests

Parser, units, stiffness, solver vs analytical, load cases/combinations, recovery, envelopes, fixture comparison, API backend switch.

---

# 27. Definition of Done

Do not consider this complete because the app starts.

Minimum:

1. Existing architecture understood.
2. OpenSTAAD dependency points identified.
3. Required STAAD syntax documented.
4. Required analysis functionality documented.
5. Canonical structural model exists.
6. `.std` parser exists for required syntax.
7. Solver exists for the required structural system.
8. Unit normalization exists.
9. Load cases work.
10. Required combinations/envelopes work.
11. Member internal forces recoverable.
12. Governing stations identifiable.
13. Displacement results recoverable.
14. Existing result contract generated for all three flows.
15. Real sample `.std` processed.
16. Results compared quantitatively to STAAD/OpenSTAAD reference JSON.
17. Differences documented.
18. Automated regression tests exist.
19. Unsupported functionality fails explicitly.
20. No proprietary STAAD implementation copied.
21. Abort works without killing STAAD when using in-house backend.
22. API job contract unchanged.

---

# 28. Important Engineering Principle

**Correctness comes before feature breadth.**

Better:

```text
100% correct → limited supported feature set
```

than:

```text
wide feature set → untrusted engineering results
```

Incorrect results can directly affect facade design decisions.

---

# 29. How You Should Work

Operate as a senior structural-software engineer.

For every major decision:

1. Inspect existing code.
2. Identify the actual requirement.
3. Explain the engineering/numerical implication.
4. Choose the simplest correct approach.
5. Implement it.
6. Add tests.
7. Validate against reference data.
8. Report discrepancies.
9. Continue only after understanding the discrepancy.

When uncertain, **do not guess silently**. Mark:

```text
CONFIRMED FROM REPOSITORY
INFERRED FROM CURRENT BEHAVIOR
ENGINEERING ASSUMPTION
REQUIRES VALIDATION
NOT CURRENTLY SUPPORTED
```

---

# 30. First Task — DO NOT CODE Yet

Your first response should be a comprehensive repository analysis that **verifies and extends §0**.

Do NOT immediately modify production code.

Produce:

1. Current architecture
2. Current STAAD/OpenSTAAD dependency map
3. Exact result-generation flow (all three extraction flows)
4. `.std` feature inventory (fixture + any attached samples)
5. Required structural-analysis capabilities
6. Candidate solver architecture
7. Risks and numerical challenges (especially FLOOR LOAD, BETA, UNIT, releases)
8. Recommended implementation phases
9. Files/classes that will likely need modification
10. Features that should explicitly **not** be implemented initially

Then consume the supplied:

```text
sample .std file(s)
reference result JSON
```

and refine the plan.

Only after that analysis should implementation begin.

---

# 31. Suggested first code-touch files (after analysis)

Likely **new**:

```text
engine/  (parser, model, units, loads, solver, recovery, adapter)
docs/CURRENT_ARCHITECTURE.md
docs/INHOUSE_SOLVER_*.md
tests for engine/
```

Likely **modify later**:

```text
app/core/config.py                 # ANALYSIS_BACKEND
app/services/staad_runner.py       # dispatch in-house vs OpenSTAAD child
app/job_manager.py                 # abort without STAAD kill in in-house mode
app/services/health_checks.py      # in-house readiness
app/main.py                        # only if health semantics change
staad-max-extractor/extractor.py   # result getters behind a backend protocol
requirements.txt                   # numpy if used
README.md / docs/openapi.yaml      # backend note; do not break the contract
```

Prefer introducing a small `AnalysisBackend` protocol:

```text
analyze(std_path, config, abort_check, progress) -> raw results
```

with two implementations: `OpenStaadBackend` (current) and `InHouseBackend` (new). Envelope/classification stay above the backend.

---

# Final Goal

Build a maintainable **in-house structural-analysis engine for NextGen Facade** that can replace the currently required STAAD.Pro/OpenSTAAD analysis/extraction dependency for the application’s supported structural models.

The engine must be:

```text
Independent
Deterministic
Testable
Numerically sound
Unit-safe
Extensible
Facade-aware at the result layer
Compatible with the existing job API and JSON contract
```

Most importantly:

**Do not optimize for making the sample output look correct. Optimize for implementing the underlying structural-analysis behavior correctly and proving that correctness through independent tests and comparison against the existing reference solver.**
