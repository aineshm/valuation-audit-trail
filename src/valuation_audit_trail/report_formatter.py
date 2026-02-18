"""Human-readable report formatter for valuation outputs.

This module generates Markdown reports from JSON report dictionaries,
providing auditors with narrative summaries of methodology, selected comps,
multiples analysis, and valuation conclusions.
"""

from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════
# Assumption Descriptions
# ═══════════════════════════════════════════════════════════════

ASSUMPTION_DESCRIPTIONS = {
    "outlier_policy": {
        "none": "No outlier treatment applied. All raw multiples are used in quantile calculations without modification.",
        "trim": "Outliers are removed from the dataset. Multiples beyond the specified quantile thresholds are excluded from analysis, reducing sensitivity to extreme values.",
        "winsorize": "Outliers are clamped to boundary values. Extreme multiples are adjusted to the specified quantile thresholds, preserving sample size while reducing outlier impact.",
    },
    "outlier_quantile": "Defines the tail fraction treated as outliers (e.g., 0.1 = bottom 10% and top 10%). Lower values are more conservative, treating fewer observations as outliers.",
    "quantile_method": {
        "nearest_rank": "Uses the nearest-rank method for computing quantiles (also known as the exclusive method). Selects the observation at the nearest integer rank position.",
        "linear_interpolation": "Uses linear interpolation between observations for quantile calculation (also known as the inclusive method). This is the recommended industry-standard approach for continuous distributions.",
    },
}

METHODOLOGY_CITATIONS = {
    "relevance_based_ranking": {
        "title": "Relevance-Based Comp Selection",
        "description": "Comps are ranked by revenue proximity to the subject company. This ensures the most economically similar peers are selected, improving valuation comparability.",
        "rationale": "Revenue size is a strong proxy for business scale, growth stage, and market positioning. Companies with similar revenues face comparable operating dynamics and capital market expectations.",
    },
    "lexicographic_ranking": {
        "title": "Deterministic Lexicographic Ranking",
        "description": "Comps are sorted alphabetically by specified fields (e.g., ticker, company ID) to ensure reproducible selection.",
        "rationale": "While arbitrary from an economic perspective, this method guarantees bit-identical results across runs when the same sort key is provided.",
    },
}


def format_markdown_report(report_dict: dict[str, Any]) -> str:
    """Generate a human-readable Markdown report from a JSON report dict.

    Args:
        report_dict: The complete report dictionary from cli.py

    Returns:
        Markdown-formatted report string
    """
    lines: list[str] = []

    # ═══════════════════════════════════════════════════════════════
    # Header
    # ═══════════════════════════════════════════════════════════════
    lines.append("# Valuation Report")
    lines.append("")
    lines.append(f"**Request ID:** {report_dict['request_id']}")
    lines.append(f"**As of Date:** {report_dict['as_of_date']}")
    lines.append(f"**Currency:** {report_dict['currency']}")
    lines.append(f"**Status:** {report_dict['status']}")
    lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # Executive Summary
    # ═══════════════════════════════════════════════════════════════
    lines.append("## Executive Summary")
    lines.append("")

    if report_dict["status"] == "ok":
        val = report_dict["valuation"]
        fv = val["fair_value"]
        lines.append(f"**Method:** {val['method']}")
        lines.append(f"**Fair Value (Point):** {fv['currency']} {fv['point']:,.2f}")
        lines.append(f"**Valuation Range:** {fv['currency']} {fv['range']['low']:,.2f} – {fv['range']['high']:,.2f}")
        lines.append(f"**Range Basis:** {fv['range_basis']}")
        lines.append("")
    else:
        lines.append("**Valuation could not be computed due to errors.**")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # Subject Company
    # ═══════════════════════════════════════════════════════════════
    lines.append("## Subject Company")
    lines.append("")
    key_inputs = report_dict["key_inputs"]
    lines.append(f"**Company ID:** {key_inputs['subject_company_id']}")
    lines.append(f"**Revenue (LTM):** {report_dict['currency']} {key_inputs['subject_revenue_ltm']:,.2f}")
    lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # Methodology
    # ═══════════════════════════════════════════════════════════════
    lines.append("## Methodology")
    lines.append("")
    lines.append("This valuation uses a **comparable companies (comps) analysis** based on EV/Revenue multiples.")
    lines.append("")

    comps_result = report_dict["comps_selection_result"]
    requested = comps_result["requested"]

    lines.append("### Selection Criteria")
    lines.append("")
    lines.append(f"**Universe:** {requested['universe']}")
    lines.append(f"**Maximum Comps:** {requested['max_comps']}")

    # Ranking strategy with citation
    sort_key_used = comps_result.get("sort_key_used", [])
    is_relevance_based = sort_key_used == ["relevance_based"] or requested.get("sort_key") is None
    
    if is_relevance_based:
        lines.append(f"**Ranking Strategy:** Relevance-based (revenue proximity to subject)")
    else:
        lines.append(f"**Ranking Strategy:** Lexicographic sort by {requested.get('sort_key', [])}")
    lines.append("")
    
    # Add methodology citation
    lines.append("### Ranking Methodology")
    lines.append("")
    if is_relevance_based:
        citation = METHODOLOGY_CITATIONS["relevance_based_ranking"]
        lines.append(f"**{citation['title']}**")
        lines.append("")
        lines.append(f"*Description:* {citation['description']}")
        lines.append("")
        lines.append(f"*Rationale:* {citation['rationale']}")
        lines.append("")
    else:
        citation = METHODOLOGY_CITATIONS["lexicographic_ranking"]
        lines.append(f"**{citation['title']}**")
        lines.append("")
        lines.append(f"*Description:* {citation['description']}")
        lines.append("")
        lines.append(f"*Rationale:* {citation['rationale']}")
        lines.append("")

    # Filters
    filters = requested.get("filters", {})
    if filters:
        lines.append("**Filters Applied:**")
        lines.append("")
        if filters.get("sector"):
            lines.append(f"- **Sector:** {', '.join(filters['sector'])}")
        if filters.get("size"):
            lines.append(f"- **Size:** {', '.join(filters['size'])}")
        if filters.get("industry_keywords"):
            lines.append(f"- **Industry Keywords:** {', '.join(filters['industry_keywords'])}")
        if filters.get("geographies"):
            lines.append(f"- **Geographies:** {', '.join(filters['geographies'])}")
        if filters.get("revenue_band"):
            rb = filters["revenue_band"]
            lines.append(f"- **Revenue Band:** {rb['min']:,.0f} – {rb['max']:,.0f}")
        lines.append("")

    # Assumptions with descriptions
    lines.append("### Key Assumptions")
    lines.append("")
    lines.append("The following assumptions were applied in this valuation analysis:")
    lines.append("")
    assumptions = report_dict["assumptions_used"]
    for asm in assumptions:
        asm_name = asm['name']
        asm_value = asm['value']
        lines.append(f"**{asm_name}:** `{asm_value}`")
        
        # Add description for this assumption
        if asm_name == "outlier_policy" and asm_value in ASSUMPTION_DESCRIPTIONS["outlier_policy"]:
            lines.append(f"  - *{ASSUMPTION_DESCRIPTIONS['outlier_policy'][asm_value]}*")
        elif asm_name == "outlier_quantile":
            lines.append(f"  - *{ASSUMPTION_DESCRIPTIONS['outlier_quantile']}*")
        elif asm_name == "quantile_method" and asm_value in ASSUMPTION_DESCRIPTIONS["quantile_method"]:
            lines.append(f"  - *{ASSUMPTION_DESCRIPTIONS['quantile_method'][asm_value]}*")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # Selected Comparables
    # ═══════════════════════════════════════════════════════════════
    if report_dict["status"] == "ok":
        lines.append("## Selected Comparables")
        lines.append("")
        lines.append(f"**Count:** {comps_result['included_count']} comps selected from {comps_result['matched_before_limit']} candidates")
        lines.append("")

        # Build comps table
        included_details = [
            md for md in comps_result["match_details"]
            if md["included"]
        ]

        if included_details:
            lines.append("| Rank | Company ID | Ticker |")
            lines.append("|------|-----------|--------|")
            for detail in included_details:
                rank = detail.get("selection_rank", "N/A")
                lines.append(f"| {rank} | {detail['company_id']} | {detail['ticker']} |")
            lines.append("")

        # Multiples analysis
        lines.append("## Multiples Analysis")
        lines.append("")

        ki = report_dict["key_inputs"]
        lines.append(f"**Raw Multiples (EV/Revenue):** {', '.join(f'{m:.2f}' for m in ki['raw_multiples'])}")
        lines.append(f"**Adjusted Multiples:** {', '.join(f'{m:.2f}' for m in ki['adjusted_multiples'])}")
        lines.append("")
        lines.append(f"**Low (Q25):** {ki['multiple_low']:.2f}x")
        lines.append(f"**Median (Q50):** {ki['median_multiple']:.2f}x")
        lines.append(f"**High (Q75):** {ki['multiple_high']:.2f}x")
        lines.append("")

        # Valuation calculation
        lines.append("## Valuation Calculation")
        lines.append("")
        lines.append(f"**Subject Revenue (LTM):** {report_dict['currency']} {ki['subject_revenue_ltm']:,.2f}")
        lines.append(f"**Median Multiple:** {ki['median_multiple']:.2f}x")
        lines.append(f"**Fair Value (Point):** {report_dict['currency']} {ki['subject_revenue_ltm'] * ki['median_multiple']:,.2f}")
        lines.append("")
        lines.append(f"**Low (Q25 × Revenue):** {report_dict['currency']} {ki['subject_revenue_ltm'] * ki['multiple_low']:,.2f}")
        lines.append(f"**High (Q75 × Revenue):** {report_dict['currency']} {ki['subject_revenue_ltm'] * ki['multiple_high']:,.2f}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # Warnings and Errors
    # ═══════════════════════════════════════════════════════════════
    warnings = report_dict.get("warnings", [])
    errors = report_dict.get("errors", [])

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- **{w['code']}:** {w['message']}")
        lines.append("")

    if errors:
        lines.append("## Errors")
        lines.append("")
        for e in errors:
            lines.append(f"- **{e['code']}:** {e['message']}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # Data Sources & Citations
    # ═══════════════════════════════════════════════════════════════
    lines.append("## Data Sources & Citations")
    lines.append("")
    lines.append("This valuation relies on the following data sources:")
    lines.append("")
    sources = report_dict.get("sources", [])
    for src in sources:
        lines.append(f"### {src['id']}")
        lines.append("")
        lines.append(f"**Provider:** {src['provider']}")
        lines.append("")
        lines.append(f"**Dataset:** {src['dataset']} (version {src['dataset_version']})")
        lines.append("")
        lines.append(f"**Hash:** {src['dataset_hash']}")
        lines.append("")
        lines.append(f"**Citation:**")
        lines.append(f"> {src['citation']}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # Run Manifest
    # ═══════════════════════════════════════════════════════════════
    lines.append("## Run Manifest")
    lines.append("")
    manifest = report_dict.get("run_manifest", {})
    if manifest:
        lines.append(f"**Timestamp (UTC):** {manifest.get('timestamp_utc', 'N/A')}")
        lines.append(f"**Engine Version:** {manifest.get('engine_version', 'N/A')}")
        lines.append(f"**Input Hash:** {manifest.get('input_hash', 'N/A')}")
        lines.append(f"**Output Hash:** {manifest.get('output_hash', 'N/A')}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # Footer
    # ═══════════════════════════════════════════════════════════════
    lines.append("---")
    lines.append("")
    lines.append("*This report was generated by the valuation-audit-trail engine.*")
    lines.append("*All outputs are deterministic and traceable via the provenance DAG.*")
    lines.append("")

    return "\n".join(lines)
