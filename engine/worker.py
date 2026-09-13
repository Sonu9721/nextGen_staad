"""Spawn target isolated from the legacy standalone app.py module name."""


def analyze_job(channel, event, path, flow, include_combinations, request):
    from .adapter import analyze_file
    from .errors import AnalysisError

    try:
        payload = analyze_file(
            path,
            flow=flow,
            include_combinations=include_combinations,
            generation_request_path=request,
            abort_check=event.is_set,
            progress=lambda percent, message: channel.put(
                {"progress": [percent, message]}
            ),
        )
        channel.put({"result": payload})
    except AnalysisError as exc:
        channel.put({"error": str(exc), "code": exc.code, "stage": exc.stage})
    except Exception as exc:
        channel.put(
            {
                "error": f"{type(exc).__name__}: {exc}",
                "code": "NUMERICAL_FAILURE",
                "stage": "execution",
            }
        )
