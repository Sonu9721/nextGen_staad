import argparse
import json
from pathlib import Path
from .adapter import analyze_file
from .errors import AnalysisError


def main():
    parser = argparse.ArgumentParser(
        description="Analyze a supported STAAD space-frame model without STAAD.Pro."
    )
    parser.add_argument("model", type=Path)
    parser.add_argument(
        "--flow", choices=["standard", "fully_unitized", "casement"], default="standard"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--generation-request", type=Path)
    parser.add_argument("--primary-only", action="store_true")
    args = parser.parse_args()
    try:
        result = analyze_file(
            args.model,
            flow=args.flow,
            include_combinations=not args.primary_only,
            generation_request_path=args.generation_request,
        )
        text = json.dumps(result, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
            print(f"Saved {args.output}")
        else:
            print(text)
    except AnalysisError as exc:
        print(
            json.dumps(
                {"error_code": exc.code, "message": str(exc), "stage": exc.stage}
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
