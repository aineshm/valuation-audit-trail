"""Error model boundary for coded warnings/errors with JSON path references.

Every error/warning code used by the engine is declared here as a constant.
The Issue dataclass matches the schema `$defs/issue` (code, message, json_path).
Validation helpers return lists of Issue objects; they never raise exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Hard thresholds (not user-configurable; change here if policy changes)
# ---------------------------------------------------------------------------
MIN_COMPS: int = 3  # below this → E_TOO_FEW_COMPS (status=error)
LOW_COMPS_THRESHOLD: int = 5  # at-or-below this (but >= MIN) → W_LOW_COMP_COUNT

# ---------------------------------------------------------------------------
# Error codes — each prevents valuation (status="error", valuation=null)
# ---------------------------------------------------------------------------
E_INVALID_REVENUE_BAND = "E_INVALID_REVENUE_BAND"
E_REVENUE_NOT_POSITIVE = "E_REVENUE_NOT_POSITIVE"
E_NO_COMPS = "E_NO_COMPS"
E_TOO_FEW_COMPS = "E_TOO_FEW_COMPS"

# ---------------------------------------------------------------------------
# Warning codes — valuation proceeds, but flag reliability concern
# ---------------------------------------------------------------------------
W_LOW_COMP_COUNT = "W_LOW_COMP_COUNT"


@dataclass(frozen=True, slots=True)
class Issue:
    """A single error or warning entry in the report.

    Mirrors report.schema.json → $defs/issue:
      { "code": str, "message": str, "json_path": str }
    """

    code: str
    message: str
    json_path: str

    def to_dict(self) -> dict:
        """Serialize to the JSON-schema-aligned dict."""
        return {"code": self.code, "message": self.message, "json_path": self.json_path}


# ---------------------------------------------------------------------------
# Pre-flight validation (runs before any computation)
# ---------------------------------------------------------------------------

def validate_request(request: dict) -> tuple[list[Issue], list[Issue]]:
    """Validate a parsed request dict against business rules.

    Returns:
        (errors, warnings) — both are lists of Issue.
        If errors is non-empty the engine must short-circuit to status="error".

    Checks performed (in order):
        1. subject.revenue_ltm > 0                → E_REVENUE_NOT_POSITIVE
        2. revenue_band.min <= revenue_band.max    → E_INVALID_REVENUE_BAND
    """
    errors: list[Issue] = []
    warnings: list[Issue] = []

    subject = request.get("subject")
    if isinstance(subject, dict):
        revenue_ltm = subject.get("revenue_ltm")
        if isinstance(revenue_ltm, (int, float)) and revenue_ltm <= 0:
            errors.append(
                Issue(
                    code=E_REVENUE_NOT_POSITIVE,
                    message=f"Subject revenue_ltm must be > 0; got {revenue_ltm}",
                    json_path="$.subject.revenue_ltm",
                )
            )

    comps_selection = request.get("comps_selection")
    if isinstance(comps_selection, dict):
        filters = comps_selection.get("filters")
        if isinstance(filters, dict):
            revenue_band = filters.get("revenue_band")
            if isinstance(revenue_band, dict):
                min_value = revenue_band.get("min")
                max_value = revenue_band.get("max")
                if (
                    isinstance(min_value, (int, float))
                    and isinstance(max_value, (int, float))
                    and min_value > max_value
                ):
                    errors.append(
                        Issue(
                            code=E_INVALID_REVENUE_BAND,
                            message=(
                                f"revenue_band.min ({min_value}) must be <= "
                                f"revenue_band.max ({max_value})"
                            ),
                            json_path="$.comps_selection.filters.revenue_band",
                        )
                    )

    return errors, warnings


def validate_comps_count(included_count: int) -> tuple[list[Issue], list[Issue]]:
    """Check comp count against MIN_COMPS / LOW_COMPS_THRESHOLD.

    Returns:
        (errors, warnings)
        - included_count == 0       → E_NO_COMPS
        - included_count < MIN_COMPS → E_TOO_FEW_COMPS
        - included_count <= LOW_COMPS_THRESHOLD → W_LOW_COMP_COUNT
    """
    if included_count == 0:
        return (
            [
                Issue(
                    code=E_NO_COMPS,
                    message="No comps matched the specified filters; cannot compute valuation",
                    json_path="$.comps_selection.filters",
                )
            ],
            [],
        )

    if included_count < MIN_COMPS:
        return (
            [
                Issue(
                    code=E_TOO_FEW_COMPS,
                    message=(
                        f"Only {included_count} comps available; minimum {MIN_COMPS} "
                        "required for reliable valuation"
                    ),
                    json_path="$.comps_selection_result.included_count",
                )
            ],
            [],
        )

    if included_count <= LOW_COMPS_THRESHOLD:
        return (
            [],
            [
                Issue(
                    code=W_LOW_COMP_COUNT,
                    message=(
                        f"Only {included_count} comps available; consider expanding "
                        "filters for more robust valuation"
                    ),
                    json_path="$.comps_selection_result.included_count",
                )
            ],
        )

    return [], []
