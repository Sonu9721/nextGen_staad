"""Recover the supplied Sample 6 line-oriented export without changing its values.

This is an explicit import utility, not a permissive JSON parser in the service.
Only object-key separators and JSON string quotes are reconstructed. Characters
already missing from string values (notably Windows paths) cannot be recovered.
"""

import json
from pathlib import Path
import re


def normalize_unquoted_export(text):
    if '"' in text or ":" in text or "\\" in text:
        raise ValueError("Expected the fully unquoted, colon-free Sample 6 format")
    stack, output = [], []

    def scalar(value):
        if value in {"[]", "{}"}:
            return value
        if value in {"true", "false", "null"} or re.fullmatch(
            r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value
        ):
            return value
        if not value or any(c in value for c in "{}[],"):
            raise ValueError("Ambiguous scalar in unquoted reference")
        return json.dumps(value, ensure_ascii=False)

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        comma = "," if line.endswith(",") else ""
        token = line[:-1].rstrip() if comma else line
        if token in {"}", "]"}:
            if not stack or stack.pop() != {"}": "{", "]": "["}[token]:
                raise ValueError(f"Unbalanced container on line {lineno}")
            output.append(token + comma)
            continue
        if not stack:
            if output or token != "{" or comma:
                raise ValueError("A single root object is required")
            stack.append("{")
            output.append("{")
            continue
        prefix = ""
        if stack[-1] == "{":
            parts = token.split(maxsplit=1)
            if len(parts) != 2 or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", parts[0]):
                raise ValueError(f"Invalid object field on line {lineno}")
            prefix, token = json.dumps(parts[0]) + ": ", parts[1]
        if token in {"{", "["}:
            if comma:
                raise ValueError(f"Unexpected comma on line {lineno}")
            stack.append(token)
            output.append(prefix + token)
        else:
            output.append(prefix + scalar(token) + comma)
    if stack:
        raise ValueError("Unclosed container")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate key: {key}")
            result[key] = value
        return result

    return json.loads("\n".join(output), object_pairs_hook=unique_object)


def main():
    folder = Path(__file__).resolve().parents[1] / "examples/sample6"
    data = normalize_unquoted_export(
        (folder / "output.txt").read_text(encoding="utf-8")
    )
    assert data["status"] == "succeeded" and isinstance(data["result"], dict)
    (folder / "reference.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print("Sample 6 reference.json written; original output.txt preserved.")


if __name__ == "__main__":
    main()
