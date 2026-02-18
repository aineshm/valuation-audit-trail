"""CLI boundary for request ingestion, execution orchestration, and report emission.

Usage:
    python -m valuation_audit_trail.cli --input request.json [--output report.json]
    python -m valuation_audit_trail.cli --input request.json --format markdown [--output report.md]
    python -m valuation_audit_trail.cli --input request.json --explain "valuation.fair_value.point"

Orchestration flow (happy path):
    1.  Parse CLI args (argparse)
    2.  Load + JSON-schema-validate the request file against schemas/input.schema.json
    3.  Convert raw dict → ValuationRequest model
    4.  Run pre-flight validation (errors.validate_request)
        → If errors: short-circuit to error report
    5.  Load provider dataset (providers.load_dataset)
    6.  Run valuation pipeline (valuation.run_valuation)
        → May return additional errors/warnings
    7.  Build provenance DAG (provenance.build_provenance)
    8.  Build run manifest (manifest.build_manifest)
    9.  Assemble ValuationReport → .to_dict()
    10. Compute + patch output_hash (manifest.compute_output_hash)
    11. JSON-schema-validate report against schemas/report.schema.json
    12. Write report JSON to stdout or --output file
    13. If --explain: run explain.explain_field and emit explain JSON instead
    14. If --format markdown: emit Markdown report instead of JSON

Error path (step 4 or step 6 errors):
    Steps 7-10 are skipped for provenance; manifest is still built.
    Report has status="error", valuation=null, non-empty errors array.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from valuation_audit_trail.errors import Issue, validate_request
from valuation_audit_trail.explain import explain_field
from valuation_audit_trail.manifest import (
    build_manifest,
    compute_output_hash,
)
from valuation_audit_trail.models import (
    AssumptionEntry,
    CompsSelectionResult,
    FairValue,
    MatchDetailEntry,
    ProviderFingerprint,
    SourceEntry,
    Valuation,
    ValuationReport,
    ValuationRequest,
)
from valuation_audit_trail.provenance import build_provenance
from valuation_audit_trail.providers import load_dataset, DatasetPayload
from valuation_audit_trail.report_formatter import format_markdown_report
from valuation_audit_trail.valuation import run_valuation

# ---------------------------------------------------------------------------
# Schema paths (relative to repo root)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INPUT_SCHEMA = _REPO_ROOT / "schemas" / "input.schema.json"
_REPORT_SCHEMA = _REPO_ROOT / "schemas" / "report.schema.json"


def main(argv: list[str] | None = None) -> int:
    """Entry point.  Returns 0 on success, 1 on validation/runtime error."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # ── 1. Load request ─────────────────────────────────────────────
    request_raw = _load_request(Path(args.input))

    # ── 2. Schema-validate request ──────────────────────────────────
    schema_errors = _validate_against_schema(request_raw, _INPUT_SCHEMA)
    if schema_errors:
        for msg in schema_errors:
            print(f"Schema error: {msg}", file=sys.stderr)
        return 1

    # ── 3. Parse into typed model ───────────────────────────────────
    request = ValuationRequest.from_dict(request_raw)

    # ── 4. Pre-flight validation ────────────────────────────────────
    preflight_errors, preflight_warnings = validate_request(request_raw)

    # ── 5. Load provider dataset ────────────────────────────────────
    dataset = load_dataset(request.provider_overrides.comps_provider)
    source_entry = dataset.source_entry
    candidates = dataset.candidates

    # Build common structures
    assumptions_used = [
        AssumptionEntry(id="asm_outlier_policy", name="outlier_policy", value=request.assumptions.outlier_policy),
        AssumptionEntry(id="asm_outlier_quantile", name="outlier_quantile", value=request.assumptions.outlier_quantile),
        AssumptionEntry(id="asm_quantile_method", name="quantile_method", value=request.assumptions.quantile_method),
    ]
    sources = [source_entry]
    provider_fps = [dataset.fingerprint]

    if preflight_errors:
        # ── Error path: short-circuit ───────────────────────────────
        report_dict = _build_error_report(
            request_raw=request_raw,
            request=request,
            errors=preflight_errors,
            warnings=preflight_warnings,
            sources=sources,
            assumptions_used=assumptions_used,
            provider_fingerprints=provider_fps,
        )
        if args.format == "markdown":
            markdown_text = format_markdown_report(report_dict)
            _emit_text(markdown_text, Path(args.output) if args.output else None)
        else:
            _emit_json(report_dict, Path(args.output) if args.output else None)
        return 1

    # ── 6. Run valuation pipeline ───────────────────────────────────
    vr = run_valuation(
        candidates=candidates,
        selection=request.comps_selection,
        assumptions=request.assumptions,
        subject_revenue_ltm=request.subject.revenue_ltm,
        rounding_decimals=request.config.rounding_decimals,
    )

    all_errors = preflight_errors + vr.errors
    all_warnings = preflight_warnings + vr.warnings

    if all_errors:
        report_dict = _build_error_report(
            request_raw=request_raw,
            request=request,
            errors=all_errors,
            warnings=all_warnings,
            sources=sources,
            assumptions_used=assumptions_used,
            provider_fingerprints=provider_fps,
        )
        if args.format == "markdown":
            markdown_text = format_markdown_report(report_dict)
            _emit_text(markdown_text, Path(args.output) if args.output else None)
        else:
            _emit_json(report_dict, Path(args.output) if args.output else None)
        return 1

    # ── 7. Build provenance DAG ─────────────────────────────────────
    prov_nodes = build_provenance(
        subject_revenue_ltm=request.subject.revenue_ltm,
        selection=request.comps_selection,
        included_candidates=vr.included_candidates,
        raw_multiples=vr.raw_multiples,
        adjusted_multiples=vr.adjusted_multiples,
        median_multiple=vr.median_multiple,
        multiple_low=vr.multiple_low,
        multiple_high=vr.multiple_high,
        ev_point=vr.ev_point,
        ev_low=vr.ev_low,
        ev_high=vr.ev_high,
        assumptions=request.assumptions,
        source_id=source_entry.id,
    )

    # ── 8. Build comps_selection_result requested echo ──────────────
    filt = request.comps_selection.filters
    filters_echo: dict[str, Any] = {}
    if filt.sector:
        filters_echo["sector"] = filt.sector
    if filt.size:
        filters_echo["size"] = filt.size
    if filt.industry_keywords:
        filters_echo["industry_keywords"] = filt.industry_keywords
    if filt.geographies:
        filters_echo["geographies"] = filt.geographies
    if filt.revenue_band is not None:
        filters_echo["revenue_band"] = {"min": filt.revenue_band.min, "max": filt.revenue_band.max}

    requested_echo = {
        "universe": request.comps_selection.universe,
        "filters": filters_echo,
        "max_comps": request.comps_selection.max_comps,
    }
    if request.comps_selection.sort_key is not None:
        requested_echo["sort_key"] = request.comps_selection.sort_key
    else:
        requested_echo["sort_key"] = None  # Explicit null for relevance-based

    included_ids = [c.company_id for c in vr.included_candidates]

    # Determine the actual sort_key used (None → "relevance_based")
    sort_key_used = request.comps_selection.sort_key if request.comps_selection.sort_key is not None else ["relevance_based"]

    comps_result = CompsSelectionResult(
        requested=requested_echo,
        dataset_universe=request.comps_selection.universe,
        sort_key_used=sort_key_used,
        matched_before_limit=vr.matched_before_limit,
        included_count=len(vr.included_candidates),
        included_company_ids=included_ids,
        match_details=vr.match_details,
    )

    # ── 9. Assemble report ──────────────────────────────────────────
    fair_value = FairValue(
        currency=request.currency,
        point=vr.ev_point,
        range_low=vr.ev_low,
        range_high=vr.ev_high,
        range_basis="q25_q75",
        provenance_node_ids={
            "point": "prov_fair_value_point",
            "range_low": "prov_fair_value_low",
            "range_high": "prov_fair_value_high",
        },
    )
    valuation = Valuation(method=request.method, fair_value=fair_value)

    key_inputs = {
        "subject_company_id": request.subject.company_id,
        "subject_revenue_ltm": request.subject.revenue_ltm,
        "selected_comp_count": len(vr.included_candidates),
        "raw_multiples": vr.raw_multiples,
        "adjusted_multiples": vr.adjusted_multiples,
        "quantile_method": request.assumptions.quantile_method,
        "multiple_low": vr.multiple_low,
        "median_multiple": vr.median_multiple,
        "multiple_high": vr.multiple_high,
    }

    manifest = build_manifest(request, provider_fps, request_raw)

    report = ValuationReport(
        request_id=request.request_id,
        as_of_date=request.as_of_date,
        currency=request.currency,
        status="ok",
        valuation=valuation,
        key_inputs=key_inputs,
        assumptions_used=assumptions_used,
        sources=sources,
        comps_selection_result=comps_result,
        provenance_nodes=prov_nodes,
        run_manifest=manifest,
        warnings=[w.to_dict() for w in all_warnings],
        errors=[],
    )

    report_dict = report.to_dict()

    # ── 10. Compute + patch output_hash ─────────────────────────────
    output_hash = compute_output_hash(report_dict)
    report_dict["run_manifest"]["output_hash"] = output_hash

    # ── 11. If --explain: emit explain JSON instead ─────────────────
    if args.explain:
        explain_out = explain_field(
            field_path=args.explain,
            provenance_nodes=prov_nodes,
            assumptions_used=assumptions_used,
            sources=sources,
        )
        _emit_json(explain_out, Path(args.output) if args.output else None)
        return 0

    # ── 12. If --format markdown: emit Markdown report ─────────────
    if args.format == "markdown":
        markdown_text = format_markdown_report(report_dict)
        _emit_text(markdown_text, Path(args.output) if args.output else None)
        return 0

    # ── 13. Emit JSON report (default) ──────────────────────────────
    _emit_json(report_dict, Path(args.output) if args.output else None)
    return 0


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser.

    Arguments:
        --input   (required)  Path to request JSON file
        --output  (optional)  Path to write report (default: stdout)
        --format  (optional)  Output format: 'json' (default) or 'markdown'
        --explain (optional)  Field path to explain (e.g. "valuation.fair_value.point")
    """
    p = argparse.ArgumentParser(
        prog="valuation-audit",
        description="Auditable comps-based EV/Revenue valuation engine",
    )
    p.add_argument(
        "--input", required=True,
        help="Path to request JSON file",
    )
    p.add_argument(
        "--output", default=None,
        help="Path to write report (default: stdout)",
    )
    p.add_argument(
        "--format", default="json", choices=["json", "markdown"],
        help="Output format: 'json' (default) or 'markdown'",
    )
    p.add_argument(
        "--explain", default=None,
        help='Field path to explain (e.g. "valuation.fair_value.point")',
    )
    return p


# ---------------------------------------------------------------------------
# Schema validation helpers
# ---------------------------------------------------------------------------

def _validate_against_schema(instance: dict, schema_path: Path) -> list[str]:
    """Validate a dict against a JSON schema file.

    Returns:
        List of validation error messages (empty = valid).

    Uses jsonschema library.  If jsonschema is not installed, returns a warning
    message but does not block execution.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        return ["jsonschema library not installed; skipping schema validation"]

    with open(schema_path) as f:
        schema = json.load(f)

    validator_cls = jsonschema.Draft202012Validator
    validator = validator_cls(schema)
    errors_out: list[str] = []
    for err in validator.iter_errors(instance):
        path = ".".join(str(p) for p in err.absolute_path)
        errors_out.append(f"{path}: {err.message}" if path else err.message)
    return errors_out


# ---------------------------------------------------------------------------
# Orchestration steps (called from main)
# ---------------------------------------------------------------------------

def _load_request(path: Path) -> dict:
    """Load and return the raw request dict from a JSON file."""
    with open(path) as f:
        return json.load(f)


def _build_error_report(
    request_raw: dict,
    request: ValuationRequest,
    errors: list[Issue],
    warnings: list[Issue],
    sources: list[SourceEntry],
    assumptions_used: list[AssumptionEntry],
    provider_fingerprints: list[ProviderFingerprint],
) -> dict:
    """Build a minimal error-path report dict.

    status="error", valuation=null, errors non-empty.
    Still includes manifest, sources, assumptions for auditability.
    """
    manifest = build_manifest(request, provider_fingerprints, request_raw)

    key_inputs: dict[str, Any] = {
        "subject_company_id": request.subject.company_id,
        "subject_revenue_ltm": request.subject.revenue_ltm,
        "selected_comp_count": 0,
    }

    # Build an empty comps_selection_result
    filt = request.comps_selection.filters
    filters_echo: dict[str, Any] = {}
    if filt.sector:
        filters_echo["sector"] = filt.sector
    if filt.size:
        filters_echo["size"] = filt.size
    if filt.industry_keywords:
        filters_echo["industry_keywords"] = filt.industry_keywords
    if filt.geographies:
        filters_echo["geographies"] = filt.geographies
    if filt.revenue_band is not None:
        filters_echo["revenue_band"] = {"min": filt.revenue_band.min, "max": filt.revenue_band.max}

    # Build requested echo with optional sort_key
    requested_echo: dict[str, Any] = {
        "universe": request.comps_selection.universe,
        "filters": filters_echo,
        "max_comps": request.comps_selection.max_comps,
    }
    if request.comps_selection.sort_key is not None:
        requested_echo["sort_key"] = request.comps_selection.sort_key
    else:
        requested_echo["sort_key"] = None

    # Determine the actual sort_key used
    sort_key_used = request.comps_selection.sort_key if request.comps_selection.sort_key is not None else ["relevance_based"]

    report_dict: dict[str, Any] = {
        "request_id": request.request_id,
        "as_of_date": request.as_of_date,
        "currency": request.currency,
        "status": "error",
        "valuation": None,
        "key_inputs": key_inputs,
        "assumptions_used": [a.to_dict() for a in assumptions_used],
        "sources": [s.to_dict() for s in sources],
        "comps_selection_result": {
            "requested": requested_echo,
            "dataset_universe": request.comps_selection.universe,
            "sort_key_used": sort_key_used,
            "matched_before_limit": 0,
            "included_count": 0,
            "included_company_ids": [],
            "match_details": [],
        },
        "provenance": {"nodes": []},
        "run_manifest": manifest.to_dict(),
        "warnings": [w.to_dict() for w in warnings],
        "errors": [e.to_dict() for e in errors],
    }

    output_hash = compute_output_hash(report_dict)
    report_dict["run_manifest"]["output_hash"] = output_hash
    return report_dict


def _emit_json(data: dict, output_path: Path | None) -> None:
    """Write a dict as pretty-printed JSON to a file or stdout."""
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _emit_text(text: str, output_path: Path | None) -> None:
    """Write plain text to a file or stdout."""
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


# ---------------------------------------------------------------------------
# Module execution support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
