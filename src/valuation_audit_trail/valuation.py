"""Valuation method boundary for comps-based EV/Revenue calculations.

This module owns ALL math:
    • filtering candidates against request criteria
    • deterministic sort + max_comps cap
    • outlier handling (none / trim / winsorize)
    • quantile computation (nearest_rank / linear_interpolation)
    • EV = revenue × multiple

It returns a ValuationResult that the orchestrator (cli.py) folds into the
final report.  Every intermediate number is surfaced for key_inputs and
provenance generation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from valuation_audit_trail.errors import (
    Issue,
    validate_comps_count,
)
from valuation_audit_trail.models import (
    Assumptions,
    CompCandidate,
    CompsSelection,
    Filters,
    MatchDetailEntry,
    MatchedFilters,
    RevenueBand,
)


# ═══════════════════════════════════════════════════════════════════════════
# Result container returned to the orchestrator
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class ValuationResult:
    """Everything the orchestrator needs from the valuation step.

    If errors is non-empty, valuation could not be computed; the orchestrator
    must emit status="error".
    """
    # --- comps selection detail ---
    match_details: list[MatchDetailEntry]
    included_candidates: list[CompCandidate]
    matched_before_limit: int

    # --- multiples pipeline (empty if error) ---
    raw_multiples: list[float]          # ev / revenue for each included comp
    adjusted_multiples: list[float]     # after outlier treatment, sorted
    median_multiple: float              # q50
    multiple_low: float                 # q25
    multiple_high: float                # q75

    # --- fair value (0.0 if error) ---
    ev_point: float
    ev_low: float
    ev_high: float

    # --- issues ---
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════════════════════════════════════

def run_valuation(
    candidates: list[CompCandidate],
    selection: CompsSelection,
    assumptions: Assumptions,
    subject_revenue_ltm: float,
    rounding_decimals: int,
) -> ValuationResult:
    """Execute the full comps_ev_revenue pipeline.

    Steps:
        1. Filter candidates against selection.filters
        2. Stable-sort survivors by selection.sort_key
        3. Cap at selection.max_comps (excess → excluded_reason="limit_max_comps")
        4. Validate comp count (E_NO_COMPS / E_TOO_FEW_COMPS / W_LOW_COMP_COUNT)
        5. Compute raw multiples  (ev / revenue_ltm for each included comp)
        6. Apply outlier policy on sorted multiples
        7. Compute q25, q50, q75 via assumptions.quantile_method
        8. EV = subject_revenue_ltm × multiple, rounded

    Returns:
        ValuationResult (errors list non-empty ⇒ valuation failed)
    """
    errors: list[Issue] = []
    warnings: list[Issue] = []
    filters = selection.filters

    # ── Step 1: evaluate every candidate ────────────────────────────
    passed: list[tuple[CompCandidate, MatchedFilters]] = []
    failed_details: list[MatchDetailEntry] = []

    for cand in candidates:
        mf = _evaluate_candidate(cand, filters, selection.universe)
        reason = _first_failure_reason(mf)
        if reason is None:
            passed.append((cand, mf))
        else:
            failed_details.append(MatchDetailEntry(
                company_id=cand.company_id,
                ticker=cand.ticker,
                included=False,
                matched_filters=mf,
                excluded_reason=reason,
                selection_rank=None,
            ))

    # ── Step 2 & 3: sort + cap ──────────────────────────────────────
    included, cap_details = _sort_and_cap(
        passed, selection.sort_key, selection.max_comps,
    )

    matched_before_limit = len(passed)
    all_details = cap_details + failed_details

    # ── Step 4: comp count validation ───────────────────────────────
    count_errors, count_warnings = validate_comps_count(len(included))
    errors.extend(count_errors)
    warnings.extend(count_warnings)

    # Early exit on hard error (no comps or too few)
    if errors:
        return ValuationResult(
            match_details=all_details,
            included_candidates=included,
            matched_before_limit=matched_before_limit,
            raw_multiples=[],
            adjusted_multiples=[],
            median_multiple=0.0,
            multiple_low=0.0,
            multiple_high=0.0,
            ev_point=0.0,
            ev_low=0.0,
            ev_high=0.0,
            errors=errors,
            warnings=warnings,
        )

    # ── Step 5: raw multiples ───────────────────────────────────────
    raw_multiples = _compute_raw_multiples(included)

    # ── Step 6: outlier treatment on sorted multiples ───────────────
    sorted_raw = sorted(raw_multiples)
    adjusted = _apply_outlier_policy(
        sorted_raw,
        assumptions.outlier_policy,
        assumptions.outlier_quantile,
        assumptions.quantile_method,
    )

    # ── Step 7: quantiles ───────────────────────────────────────────
    qm = assumptions.quantile_method
    multiple_low = round(_compute_quantile(adjusted, 0.25, qm), rounding_decimals)
    median_multiple = round(_compute_quantile(adjusted, 0.50, qm), rounding_decimals)
    multiple_high = round(_compute_quantile(adjusted, 0.75, qm), rounding_decimals)

    # ── Step 8: EV computation ──────────────────────────────────────
    ev_point = round(subject_revenue_ltm * median_multiple, rounding_decimals)
    ev_low = round(subject_revenue_ltm * multiple_low, rounding_decimals)
    ev_high = round(subject_revenue_ltm * multiple_high, rounding_decimals)

    return ValuationResult(
        match_details=all_details,
        included_candidates=included,
        matched_before_limit=matched_before_limit,
        raw_multiples=raw_multiples,
        adjusted_multiples=adjusted,
        median_multiple=median_multiple,
        multiple_low=multiple_low,
        multiple_high=multiple_high,
        ev_point=ev_point,
        ev_low=ev_low,
        ev_high=ev_high,
        errors=errors,
        warnings=warnings,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Step 1: Filtering
# ═══════════════════════════════════════════════════════════════════════════

def _evaluate_candidate(
    candidate: CompCandidate,
    filters: Filters,
    universe: str,
) -> MatchedFilters:
    """Evaluate a single candidate against all filter criteria.

    Returns a MatchedFilters with True/False for each dimension:
        - universe:           candidate.universe matches the requested universe
                              (case-insensitive).  The provider is responsible
                              for stamping each candidate with its universe at
                              load time — the valuation layer never inspects
                              the raw data source.
        - sector:             candidate.sector matches at least one of
                              filters.sector (case-insensitive exact match).
                              If filters.sector is empty → True (no filter).
        - size:               candidate.size matches at least one of
                              filters.size (case-insensitive exact match).
                              If filters.size is empty → True (no filter).
        - industry_keywords:  at least one of candidate.industry_tags
                              matches a keyword (case-insensitive substring).
                              If filters.industry_keywords is empty → True.
        - geographies:        candidate.geography is in filters.geographies
                              (case-insensitive exact match).
                              If filters.geographies is empty → True.
        - revenue_band:       min <= candidate.revenue_ltm <= max
                              (if no revenue_band filter → True)
        - ev_positive:        candidate.ev > 0
    """
    # Universe — source-agnostic: every candidate carries its own universe tag.
    universe_ok = candidate.universe.lower() == universe.lower() if universe else True

    # Sector filter (case-insensitive exact match, empty list ⇒ no filter)
    if filters.sector:
        sector_ok = candidate.sector.lower() in [s.lower() for s in filters.sector]
    else:
        sector_ok = True

    # Size filter (case-insensitive exact match, empty list ⇒ no filter)
    if filters.size:
        size_ok = candidate.size.lower() in [s.lower() for s in filters.size]
    else:
        size_ok = True

    # Industry keywords (case-insensitive substring match in any tag)
    if filters.industry_keywords:
        tags_lower = [t.lower() for t in candidate.industry_tags]
        kw_lower = [k.lower() for k in filters.industry_keywords]
        industry_ok = any(
            kw in tag for tag in tags_lower for kw in kw_lower
        )
    else:
        industry_ok = True

    # Geographies (case-insensitive exact match, empty list ⇒ no filter)
    if filters.geographies:
        geo_ok = candidate.geography.upper() in [g.upper() for g in filters.geographies]
    else:
        geo_ok = True

    # Revenue band (inclusive on both ends)
    if filters.revenue_band is not None:
        rb = filters.revenue_band
        revenue_ok = rb.min <= candidate.revenue_ltm <= rb.max
    else:
        revenue_ok = True

    # EV positive
    ev_ok = candidate.ev > 0

    return MatchedFilters(
        universe=universe_ok,
        sector=sector_ok,
        size=size_ok,
        industry_keywords=industry_ok,
        geographies=geo_ok,
        revenue_band=revenue_ok,
        ev_positive=ev_ok,
    )


def _first_failure_reason(mf: MatchedFilters) -> str | None:
    """Return the excluded_reason string for the first filter that failed, or None.

    Priority order (matches schema enum):
        universe → sector → size → industry_keywords → geographies → revenue_band → ev_not_positive
    """
    if not mf.universe:
        return "filter_universe"
    if not mf.sector:
        return "filter_sector"
    if not mf.size:
        return "filter_size"
    if not mf.industry_keywords:
        return "filter_industry_keywords"
    if not mf.geographies:
        return "filter_geographies"
    if not mf.revenue_band:
        return "filter_revenue_band"
    if not mf.ev_positive:
        return "filter_ev_not_positive"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Step 2 & 3: Sort + Cap
# ═══════════════════════════════════════════════════════════════════════════

def _sort_and_cap(
    passed: list[tuple[CompCandidate, MatchedFilters]],
    sort_key: list[str],
    max_comps: int,
) -> tuple[list[CompCandidate], list[MatchDetailEntry]]:
    """Stable-sort by sort_key, then cap at max_comps.

    Returns:
        (included_candidates, all_match_detail_entries)
        Entries beyond max_comps have excluded_reason="limit_max_comps".
        Entries that were filtered out have excluded_reason from _first_failure_reason.
    """
    # Stable-sort by sort_key — each key is an attribute of CompCandidate
    def _sort_tuple(item: tuple[CompCandidate, MatchedFilters]) -> tuple:
        c = item[0]
        return tuple(getattr(c, k, "") for k in sort_key)

    sorted_passed = sorted(passed, key=_sort_tuple)

    included: list[CompCandidate] = []
    details: list[MatchDetailEntry] = []

    for rank_idx, (cand, mf) in enumerate(sorted_passed):
        rank = rank_idx + 1  # 1-based
        if rank <= max_comps:
            included.append(cand)
            details.append(MatchDetailEntry(
                company_id=cand.company_id,
                ticker=cand.ticker,
                included=True,
                matched_filters=mf,
                excluded_reason=None,
                selection_rank=rank,
            ))
        else:
            details.append(MatchDetailEntry(
                company_id=cand.company_id,
                ticker=cand.ticker,
                included=False,
                matched_filters=mf,
                excluded_reason="limit_max_comps",
                selection_rank=rank,
            ))

    return included, details


# ═══════════════════════════════════════════════════════════════════════════
# Step 5: Raw multiples
# ═══════════════════════════════════════════════════════════════════════════

def _compute_raw_multiples(included: list[CompCandidate]) -> list[float]:
    """For each included comp: multiple = ev / revenue_ltm.

    Precondition: all included comps have revenue_ltm > 0 and ev > 0
    (guaranteed by the ev_positive filter + revenue_band filter).
    """
    return [c.ev / c.revenue_ltm for c in included]


# ═══════════════════════════════════════════════════════════════════════════
# Step 6: Outlier treatment
# ═══════════════════════════════════════════════════════════════════════════

def _apply_outlier_policy(
    sorted_multiples: list[float],
    policy: str,
    quantile: float,
    quantile_method: str,
) -> list[float]:
    """Apply outlier treatment to a SORTED list of multiples.

    Policies:
        "none"      → return as-is
        "trim"      → remove values outside [q_low, q_high] boundaries
        "winsorize" → clamp values outside [q_low, q_high] to the boundaries

    Boundaries are computed using _quantile_nearest_rank or
    _quantile_linear_interpolation depending on quantile_method.
        lower_boundary = quantile(sorted_multiples, quantile)
        upper_boundary = quantile(sorted_multiples, 1 - quantile)
    """
    if policy == "none":
        return list(sorted_multiples)

    lower = _compute_quantile(sorted_multiples, quantile, quantile_method)
    upper = _compute_quantile(sorted_multiples, 1.0 - quantile, quantile_method)

    if policy == "trim":
        return [v for v in sorted_multiples if lower <= v <= upper]

    if policy == "winsorize":
        return [max(lower, min(v, upper)) for v in sorted_multiples]

    raise ValueError(f"Unknown outlier_policy: {policy!r}")


# ═══════════════════════════════════════════════════════════════════════════
# Step 7: Quantile functions
# ═══════════════════════════════════════════════════════════════════════════

def _quantile_nearest_rank(sorted_values: list[float], q: float) -> float:
    """Nearest-rank quantile: value at index ceil(q * n) - 1  (0-based).

    Strict definition:
        rank = ceil(q * n)          # 1-based
        result = sorted_values[rank - 1]   # convert to 0-based

    Edge cases:
        q == 0 → index 0 (minimum)
        q == 1 → index n-1 (maximum)
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError("Cannot compute quantile of empty list")
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    rank = math.ceil(q * n)  # 1-based rank
    return sorted_values[rank - 1]


def _quantile_linear_interpolation(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile (matches numpy method='linear' default).

    virtual_index = q * (n - 1)
    i = floor(virtual_index)
    fraction = virtual_index - i
    result = sorted_values[i] + fraction * (sorted_values[i+1] - sorted_values[i])

    Edge: if fraction == 0 or i+1 >= n, just return sorted_values[i].
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError("Cannot compute quantile of empty list")
    if n == 1:
        return sorted_values[0]

    virtual_index = q * (n - 1)
    i = int(math.floor(virtual_index))
    fraction = virtual_index - i

    if fraction == 0.0 or i + 1 >= n:
        return sorted_values[i]

    return sorted_values[i] + fraction * (sorted_values[i + 1] - sorted_values[i])


def _compute_quantile(sorted_values: list[float], q: float, method: str) -> float:
    """Dispatch to the correct quantile implementation."""
    if method == "nearest_rank":
        return _quantile_nearest_rank(sorted_values, q)
    elif method == "linear_interpolation":
        return _quantile_linear_interpolation(sorted_values, q)
    else:
        raise ValueError(f"Unknown quantile_method: {method!r}")
