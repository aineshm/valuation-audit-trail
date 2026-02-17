"""Provenance boundary for derivation graph nodes and traceability linkage.

Builds the DAG of ProvenanceNode objects that traces every fair_value number
back to raw inputs, assumptions, and sources.

The DAG follows this topology (10 nodes for the success path):

    prov_subject_revenue                         (pass-through from request)
    prov_selected_comps                          (filter + sort + cap)
    prov_raw_multiples        ← selected_comps   (ev / revenue per comp)
    prov_adjusted_multiples   ← raw_multiples    (outlier treatment)
    prov_multiple_low         ← adjusted         (q25)
    prov_multiple_point       ← adjusted         (q50)
    prov_multiple_high        ← adjusted         (q75)
    prov_fair_value_low       ← subject_rev, q25
    prov_fair_value_point     ← subject_rev, q50
    prov_fair_value_high      ← subject_rev, q75

Each node carries:  id, field_path, formula (human-readable), inputs (dict),
assumption_ids, source_ids, output, parent_ids.
"""

from __future__ import annotations

from typing import Any

from valuation_audit_trail.models import (
    Assumptions,
    CompCandidate,
    CompsSelection,
    ProvenanceNode,
)


# ═══════════════════════════════════════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════════════════════════════════════

def build_provenance(
    *,
    subject_revenue_ltm: float,
    selection: CompsSelection,
    included_candidates: list[CompCandidate],
    raw_multiples: list[float],
    adjusted_multiples: list[float],
    median_multiple: float,
    multiple_low: float,
    multiple_high: float,
    ev_point: float,
    ev_low: float,
    ev_high: float,
    assumptions: Assumptions,
    source_id: str,
) -> list[ProvenanceNode]:
    """Build the full provenance DAG (10 nodes) for a successful valuation.

    Returns:
        Ordered list of ProvenanceNode from root inputs to final fair values.
    """
    included_ids = [c.company_id for c in included_candidates]
    n = len(adjusted_multiples)
    qm = assumptions.quantile_method

    nodes = [
        _build_subject_revenue_node(subject_revenue_ltm),
        _build_selected_comps_node(selection, included_ids, source_id),
        _build_raw_multiples_node(included_candidates, raw_multiples, source_id),
        _build_adjusted_multiples_node(
            raw_multiples, adjusted_multiples, assumptions, source_id,
        ),
        _build_quantile_node(
            "prov_multiple_low", "key_inputs.multiple_low",
            "multiple_low", 0.25, adjusted_multiples, multiple_low,
            assumptions, source_id, n,
        ),
        _build_quantile_node(
            "prov_multiple_point", "key_inputs.median_multiple",
            "median_multiple", 0.50, adjusted_multiples, median_multiple,
            assumptions, source_id, n,
        ),
        _build_quantile_node(
            "prov_multiple_high", "key_inputs.multiple_high",
            "multiple_high", 0.75, adjusted_multiples, multiple_high,
            assumptions, source_id, n,
        ),
        _build_fair_value_node(
            "prov_fair_value_point", "valuation.fair_value.point",
            subject_revenue_ltm, median_multiple, ev_point,
            "prov_multiple_point", source_id,
        ),
        _build_fair_value_node(
            "prov_fair_value_low", "valuation.fair_value.range.low",
            subject_revenue_ltm, multiple_low, ev_low,
            "prov_multiple_low", source_id,
        ),
        _build_fair_value_node(
            "prov_fair_value_high", "valuation.fair_value.range.high",
            subject_revenue_ltm, multiple_high, ev_high,
            "prov_multiple_high", source_id,
        ),
    ]
    return nodes


# ═══════════════════════════════════════════════════════════════════════════
# Individual node builders (one per DAG node)
# ═══════════════════════════════════════════════════════════════════════════

def _build_subject_revenue_node(revenue_ltm: float) -> ProvenanceNode:
    """prov_subject_revenue — pass-through of the request's subject.revenue_ltm."""
    return ProvenanceNode(
        id="prov_subject_revenue",
        field_path="key_inputs.subject_revenue_ltm",
        formula="subject_revenue_ltm = request.subject.revenue_ltm (pass-through input)",
        inputs={"revenue_ltm": revenue_ltm},
        assumption_ids=[],
        source_ids=[],
        output=revenue_ltm,
        parent_ids=[],
    )


def _build_selected_comps_node(
    selection: CompsSelection,
    included_ids: list[str],
    source_id: str,
) -> ProvenanceNode:
    """prov_selected_comps — records the filter/sort/cap operation."""
    filt = selection.filters
    filters_dict: dict[str, Any] = {}
    if filt.sector:
        filters_dict["sector"] = filt.sector
    if filt.size:
        filters_dict["size"] = filt.size
    if filt.industry_keywords:
        filters_dict["industry_keywords"] = filt.industry_keywords
    if filt.geographies:
        filters_dict["geographies"] = filt.geographies
    if filt.revenue_band is not None:
        filters_dict["revenue_band"] = {
            "min": filt.revenue_band.min,
            "max": filt.revenue_band.max,
        }

    return ProvenanceNode(
        id="prov_selected_comps",
        field_path="comps_selection_result.included_company_ids",
        formula="selected_comps = sort(filter(universe, filters), sort_key)[:max_comps]",
        inputs={
            "selection": {
                "universe": selection.universe,
                "filters": filters_dict,
                "max_comps": selection.max_comps,
                "sort_key": selection.sort_key,
            },
        },
        assumption_ids=[],
        source_ids=[source_id],
        output=included_ids,
        parent_ids=[],
    )


def _build_raw_multiples_node(
    included_candidates: list[CompCandidate],
    raw_multiples: list[float],
    source_id: str,
) -> ProvenanceNode:
    """prov_raw_multiples — ev_i / revenue_ltm_i per included comp."""
    comps_detail = [
        {
            "company_id": c.company_id,
            "ticker": c.ticker,
            "ev": c.ev,
            "revenue_ltm": c.revenue_ltm,
        }
        for c in included_candidates
    ]
    return ProvenanceNode(
        id="prov_raw_multiples",
        field_path="key_inputs.raw_multiples",
        formula="multiple_i = ev_i / revenue_ltm_i",
        inputs={"selected_comps": comps_detail},
        assumption_ids=[],
        source_ids=[source_id],
        output=raw_multiples,
        parent_ids=["prov_selected_comps"],
    )


def _build_adjusted_multiples_node(
    raw_multiples: list[float],
    adjusted_multiples: list[float],
    assumptions: Assumptions,
    source_id: str,
) -> ProvenanceNode:
    """prov_adjusted_multiples — after outlier policy is applied."""
    policy = assumptions.outlier_policy
    q = assumptions.outlier_quantile
    qm = assumptions.quantile_method

    formula = (
        f"adjusted = {policy}(sorted(raw_multiples), "
        f"quantile={q}, method={qm})"
    )

    return ProvenanceNode(
        id="prov_adjusted_multiples",
        field_path="key_inputs.adjusted_multiples",
        formula=formula,
        inputs={
            "raw_multiples": raw_multiples,
            "outlier_policy": policy,
            "outlier_quantile": q,
            "quantile_method": qm,
        },
        assumption_ids=[
            "asm_outlier_policy",
            "asm_outlier_quantile",
            "asm_quantile_method",
        ],
        source_ids=[source_id],
        output=adjusted_multiples,
        parent_ids=["prov_raw_multiples"],
    )


def _build_quantile_node(
    node_id: str,
    field_path: str,
    quantile_label: str,
    q_value: float,
    adjusted_multiples: list[float],
    output: float,
    assumptions: Assumptions,
    source_id: str,
    n: int,
) -> ProvenanceNode:
    """Shared builder for prov_multiple_low / prov_multiple_point / prov_multiple_high."""
    import math as _math
    qm = assumptions.quantile_method
    if qm == "nearest_rank":
        rank = _math.ceil(q_value * n) if n > 0 else 0
        formula = (
            f"{quantile_label} = q{int(q_value*100)}(adjusted_multiples, "
            f"method={qm}); rank=ceil({q_value}*{n})={rank}"
        )
    else:
        formula = (
            f"{quantile_label} = q{int(q_value*100)}(adjusted_multiples, "
            f"method={qm})"
        )

    return ProvenanceNode(
        id=node_id,
        field_path=field_path,
        formula=formula,
        inputs={"adjusted_multiples": adjusted_multiples},
        assumption_ids=[
            "asm_outlier_policy",
            "asm_outlier_quantile",
            "asm_quantile_method",
        ],
        source_ids=[source_id],
        output=output,
        parent_ids=["prov_adjusted_multiples"],
    )


def _build_fair_value_node(
    node_id: str,
    field_path: str,
    revenue_ltm: float,
    multiple: float,
    ev: float,
    multiple_node_id: str,
    source_id: str,
) -> ProvenanceNode:
    """Shared builder for prov_fair_value_point / _low / _high."""
    # Derive a human-readable label from the node_id
    if "point" in node_id:
        label = "EV_point"
        mult_label = "median_multiple"
    elif "low" in node_id:
        label = "EV_low"
        mult_label = "multiple_low"
    else:
        label = "EV_high"
        mult_label = "multiple_high"

    formula = f"{label} = subject_revenue_ltm * {mult_label} = {revenue_ltm} * {multiple}"

    return ProvenanceNode(
        id=node_id,
        field_path=field_path,
        formula=formula,
        inputs={"revenue_ltm": revenue_ltm, mult_label: multiple},
        assumption_ids=[],
        source_ids=[source_id],
        output=ev,
        parent_ids=["prov_subject_revenue", multiple_node_id],
    )
