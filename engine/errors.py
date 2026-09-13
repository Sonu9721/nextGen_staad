class AnalysisError(ValueError):
    def __init__(self, code, message, *, stage="input_validation", line=None):
        self.code, self.stage, self.line = code, stage, line
        super().__init__(f"Line {line}: {message}" if line else message)


def check_cancel(callback):
    if callback and callback():
        raise AnalysisError(
            "ANALYSIS_CANCELLED", "Analysis canceled.", stage="analysis"
        )
