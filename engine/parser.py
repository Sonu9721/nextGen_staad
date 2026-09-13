"""Strict, line-aware parser for the documented STAAD subset."""

from pathlib import Path
import math
import re
import numpy as np
from .errors import AnalysisError, check_cancel
from .units import Units
from .model import (
    DOFS,
    StructuralModel,
    Material,
    Member,
    Section,
    LoadCase,
    MemberLoad,
    FloorLoad,
)


def statements(text):
    pending, first = "", 0
    for line, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("*"):
            continue
        if not pending:
            first = line
        if raw.endswith("-"):
            pending += raw[:-1] + " "
            continue
        for part in (pending + raw).split(";"):
            if part.strip():
                yield first, part.strip()
        pending = ""
    if pending:
        raise AnalysisError(
            "INVALID_STD_FILE", "Unterminated continuation.", line=first
        )


def number(s):
    value = float(s.replace("D", "E"))
    if not math.isfinite(value):
        raise ValueError("Nonfinite number")
    return value


def ids(tokens, available):
    if tokens == ["ALL"]:
        return list(available)
    result, i = [], 0
    while i < len(tokens):
        start = int(tokens[i])
        i += 1
        if start <= 0:
            raise ValueError("Identifiers must be positive")
        if i < len(tokens) and tokens[i] == "TO":
            end = int(tokens[i + 1])
            i += 2
            if end < start or end - start > 100000:
                raise ValueError("Invalid TO range")
            result.extend(range(start, end + 1))
        else:
            result.append(start)
    if not result:
        raise ValueError("Empty identifier list")
    return result


def parse_std(path, abort_check=None):
    path = Path(path)
    if path.suffix.lower() != ".std" or not path.is_file():
        raise AnalysisError("INVALID_STD_FILE", "Expected an existing .std file.")
    try:
        contents = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise AnalysisError(
            "INVALID_STD_FILE", "Cannot read model as UTF-8 text."
        ) from exc
    return parse_text(contents, abort_check)


def parse_text(text, abort_check=None):
    model, units, section, material, current = StructuralModel(), None, "", None, None
    analysis, header, finished = False, False, False
    for line, raw in statements(text):
        check_cancel(abort_check)
        t = raw.upper().split()
        u = " ".join(t)
        try:
            if finished:
                raise ValueError("Content after FINISH")
            if u == "STAAD SPACE":
                if header:
                    raise ValueError("Duplicate model header")
                header = True
                continue
            if u == "START JOB INFORMATION":
                section = "job"
                continue
            if u == "END JOB INFORMATION":
                section = ""
                continue
            if section == "job":
                continue
            if u.startswith("INPUT WIDTH "):
                continue
            if t[0] == "UNIT":
                units = Units.parse(t[1:])
                continue
            if u == "PERFORM ANALYSIS":
                if analysis:
                    raise ValueError("Multiple analysis passes are unsupported")
                analysis = True
                model.file_units = units or Units()
                section = ""
                continue
            if u == "FINISH":
                finished = True
                continue
            if analysis:
                if u.startswith("PRINT "):
                    continue
                raise AnalysisError(
                    "UNSUPPORTED_ANALYSIS_FEATURE",
                    "Commands after analysis are unsupported.",
                )
            blocks = {
                "JOINT COORDINATES": "nodes",
                "MEMBER INCIDENCES": "members",
                "DEFINE MATERIAL START": "materials",
                "END DEFINE MATERIAL": "",
                "CONSTANTS": "constants",
                "MEMBER PROPERTY AMERICAN": "properties",
                "SUPPORTS": "supports",
                "MEMBER RELEASE": "releases",
                "MEMBER LOAD": "member_load",
                "JOINT LOAD": "joint_load",
                "FLOOR LOAD": "floor_load",
            }
            if u in blocks:
                section = blocks[u]
                if section.endswith("_load") and not isinstance(current, LoadCase):
                    raise ValueError("Load block outside primary case")
                continue
            if t[:2] == ["LOAD", "COMB"]:
                cid = int(t[2])
                if cid <= 0 or cid in model.cases or cid in model.combinations:
                    raise ValueError("Duplicate/invalid case ID")
                model.combinations[cid] = {}
                current = cid
                section = "combination"
                model.titles[cid] = " ".join(raw.split()[3:])
                continue
            if t[0] == "LOAD" and len(t) > 1 and t[1].isdigit():
                cid = int(t[1])
                if cid <= 0 or cid in model.cases or cid in model.combinations:
                    raise ValueError("Duplicate/invalid case ID")
                # LOADTYPE and TITLE are labels, not loading instructions.
                if len(t) > 2 and t[2] not in {"LOADTYPE", "TITLE"}:
                    raise ValueError("Unsupported primary LOAD syntax")
                title = (
                    raw[re.search(r"\bTITLE\b", raw, re.I).end() :].strip()
                    if "TITLE" in t
                    else ""
                )
                current = model.cases[cid] = LoadCase(cid, title)
                model.titles[cid] = title
                section = "case"
                continue
            if units is None:
                raise ValueError("Explicit UNIT required before model data")
            if t[0] == "SELFWEIGHT":
                if (
                    not isinstance(current, LoadCase)
                    or len(t) != 3
                    or t[1] not in {"X", "Y", "Z"}
                ):
                    raise ValueError("Unsupported SELFWEIGHT")
                current.selfweight.append((t[1], number(t[2])))
                continue
            if section == "nodes" and t[0].isdigit():
                if len(t) != 4:
                    raise ValueError("Joint requires id x y z")
                n = int(t[0])
                if n <= 0 or n in model.nodes:
                    raise AnalysisError("INVALID_NODE", f"Duplicate/invalid joint {n}")
                model.nodes[n] = np.array([number(v) * units.l for v in t[1:]])
            elif section == "members" and t[0].isdigit():
                if len(t) != 3:
                    raise ValueError("Member requires id start end")
                mid, n1, n2 = map(int, t)
                if mid <= 0 or mid in model.members:
                    raise AnalysisError(
                        "INVALID_MEMBER", f"Duplicate/invalid member {mid}"
                    )
                model.members[mid] = Member(mid, n1, n2)
            elif section == "materials":
                if t[0] == "ISOTROPIC" and len(t) == 2:
                    if t[1] in model.materials:
                        raise ValueError("Duplicate material")
                    material = model.materials[t[1]] = Material(t[1])
                elif (
                    material
                    and t[0] in {"E", "POISSON", "DENSITY", "ALPHA", "DAMP"}
                    and len(t) == 2
                ):
                    v = number(t[1])
                    if t[0] == "E":
                        material.e = v * units.f / units.l**2
                    if t[0] == "POISSON":
                        material.poisson = v
                    if t[0] == "DENSITY":
                        material.density = v * units.f / units.l**3
                elif material and t[0] in {"TYPE", "STRENGTH"}:
                    pass
                else:
                    raise AnalysisError("UNSUPPORTED_STAAD_COMMAND", raw)
            elif section == "constants" and t[0] in {"BETA", "MATERIAL"}:
                for mid in ids(t[2:], model.members):
                    if mid not in model.members:
                        raise AnalysisError("INVALID_MEMBER", f"Unknown member {mid}")
                    if t[0] == "BETA":
                        model.members[mid].beta = number(t[1])
                    else:
                        model.members[mid].material = t[1]
            elif section == "properties" and "PRIS" in t:
                split = t.index("PRIS")
                prop = t[split + 1 :]
                if len(prop) % 2:
                    raise ValueError("Malformed PRIS properties")
                values = {}
                for key, value in zip(prop[::2], prop[1::2]):
                    if (
                        key not in {"AX", "IX", "IY", "IZ", "YD", "ZD", "AY", "AZ"}
                        or key in values
                    ):
                        raise ValueError(
                            f"Unsupported/duplicate section property {key}"
                        )
                    power = (
                        4
                        if key in {"IX", "IY", "IZ"}
                        else 1
                        if key in {"YD", "ZD"}
                        else 2
                    )
                    values[key.lower()] = number(value) * units.l**power
                if not {"ax", "ix", "iy", "iz"} <= values.keys():
                    raise AnalysisError(
                        "INVALID_SECTION", "Explicit AX IX IY IZ required"
                    )
                for mid in ids(t[:split], model.members):
                    model.members[mid].section = Section(**values)
            elif section == "supports" and ("PINNED" in t or "FIXED" in t):
                k = t.index("PINNED") if "PINNED" in t else t.index("FIXED")
                restrained = {0, 1, 2} if t[k] == "PINNED" else set(range(6))
                if len(t) > k + 1:
                    if t[k : k + 2] != ["FIXED", "BUT"] or not t[k + 2 :]:
                        raise ValueError("Unsupported support")
                    restrained -= {DOFS.index(v) for v in t[k + 2 :]}
                for n in ids(t[:k], model.nodes):
                    if n in model.supports:
                        raise ValueError(f"Duplicate support at node {n}")
                    model.supports[n] = set(restrained)
            elif section == "releases" and ("START" in t or "END" in t):
                k = t.index("START") if "START" in t else t.index("END")
                offset = 0 if t[k] == "START" else 6
                if not t[k + 1 :]:
                    raise ValueError("Empty release")
                release = {DOFS.index(v) + offset for v in t[k + 1 :]}
                for mid in ids(t[:k], model.members):
                    model.members[mid].releases.update(release)
            elif section == "joint_load" and t[0].isdigit():
                k = next(i for i, v in enumerate(t) if v in DOFS)
                values = t[k:]
                if len(values) % 2:
                    raise ValueError("Malformed joint load")
                force = np.zeros(6)
                for axis, value in zip(values[::2], values[1::2]):
                    index = DOFS.index(axis)
                    force[index] += (
                        number(value) * units.f * (units.l if index > 2 else 1.0)
                    )
                for n in ids(t[:k], model.nodes):
                    current.nodal[n] = current.nodal.get(n, np.zeros(6)) + force
            elif section == "member_load" and any(
                v in t for v in ("CON", "CMOM", "UNI")
            ):
                k = next(i for i, v in enumerate(t) if v in {"CON", "CMOM", "UNI"})
                kind, direction = t[k : k + 2]
                vals = [number(v) for v in t[k + 2 :]]
                if direction not in {"X", "Y", "Z", "GX", "GY", "GZ"}:
                    raise ValueError("Unsupported member load direction")
                point = kind in {"CON", "CMOM"}
                if point and len(vals) != 2:
                    raise ValueError(f"{kind} requires magnitude and distance")
                if kind == "UNI" and len(vals) not in {1, 3}:
                    raise ValueError("UNI requires intensity, optional start/end")
                if len(vals) > 1 and any(v < 0 for v in vals[1:]):
                    raise ValueError("Member load distances must be nonnegative")
                a = vals[1] * units.l if len(vals) > 1 else 0.0
                b = a if point else vals[2] * units.l if len(vals) == 3 else -1.0
                q = (
                    vals[0]
                    * units.f
                    * (units.l if kind == "CMOM" else 1 if point else 1 / units.l)
                )
                for mid in ids(t[:k], model.members):
                    current.member.append(
                        MemberLoad(mid, direction, a, b, q, q, point, kind == "CMOM")
                    )
            elif section == "floor_load" and t[0] in {"XRANGE", "YRANGE", "ZRANGE"}:
                ranges, pressure, direction, i = {}, None, None, 0
                while i < len(t):
                    key = t[i]
                    if key in {"XRANGE", "YRANGE", "ZRANGE"}:
                        if key[0] in ranges:
                            raise ValueError("Duplicate floor range")
                        ranges[key[0]] = (
                            number(t[i + 1]) * units.l,
                            number(t[i + 2]) * units.l,
                        )
                        i += 3
                    elif key == "FLOAD" and pressure is None:
                        pressure = number(t[i + 1]) * units.f / units.l**2
                        i += 2
                    elif key in {"GX", "GY", "GZ"} and direction is None:
                        direction = key
                        i += 1
                    else:
                        raise ValueError(f"Unsupported floor option {key}")
                if (
                    direction is None
                    or pressure is None
                    or any(a > b for a, b in ranges.values())
                ):
                    raise ValueError(
                        "Explicit valid floor direction/pressure/ranges required"
                    )
                current.floor.append(FloorLoad(direction, pressure, ranges, line))
            elif section == "combination" and t[0].isdigit():
                if len(t) % 2:
                    raise ValueError("Combination requires case-factor pairs")
                for case, factor in zip(t[::2], t[1::2]):
                    cid = int(case)
                    model.combinations[current][cid] = model.combinations[current].get(
                        cid, 0.0
                    ) + number(factor)
            else:
                raise AnalysisError(
                    "UNSUPPORTED_STAAD_COMMAND", f"Unsupported command: {raw}"
                )
        except AnalysisError as exc:
            if exc.line:
                raise
            raise AnalysisError(exc.code, str(exc), line=line) from exc
        except (ValueError, KeyError, IndexError, StopIteration, TypeError) as exc:
            raise AnalysisError("INVALID_STD_FILE", f"{raw}: {exc}", line=line) from exc
    if not header or not analysis or not finished or section == "job":
        raise AnalysisError(
            "INVALID_STD_FILE", "STAAD SPACE, PERFORM ANALYSIS and FINISH are required."
        )
    model.validate()
    return model
