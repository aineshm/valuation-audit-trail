"""Run manifest boundary for deterministic metadata and fingerprint capture.

The manifest captures everything needed to answer "could I reproduce this
exact report?":
    • timestamp_utc
    • engine_version
    • config_snapshot   (echoes ALL result-driving inputs)
    • input_hash        (sha256 of canonical request JSON)
    • provider_fingerprints (hash of each data file used)
    • output_hash       (sha256 of canonical report JSON, excluding output_hash itself)

Canonical JSON means: json.dumps(obj, sort_keys=True, separators=(',', ':'))
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from valuation_audit_trail.models import (
    ProviderFingerprint,
    RunManifest,
    ValuationRequest,
)


def build_manifest(
    request: ValuationRequest,
    provider_fingerprints: list[ProviderFingerprint],
    request_raw: dict,
) -> RunManifest:
    """Build a RunManifest for the report.

    The output_hash is set to a placeholder here; the orchestrator must call
    compute_output_hash() after the full report dict is assembled, then patch it.

    Args:
        request:              Typed request object (for engine_version, config fields).
        provider_fingerprints: One per provider used (currently just mock_v1).
        request_raw:          The raw dict as loaded from JSON (for input_hash).

    Returns:
        RunManifest with output_hash = "sha256:pending"
    """
    config_snapshot = build_config_snapshot(request)
    input_hash = compute_hash(canonical_json_bytes(request_raw))

    return RunManifest(
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        engine_version=request.config.engine_version,
        config_snapshot=config_snapshot,
        input_hash=input_hash,
        provider_fingerprints=provider_fingerprints,
        output_hash="sha256:pending",
    )


def build_config_snapshot(request: ValuationRequest) -> dict:
    """Build the expanded config_snapshot dict.

    Must include (per report.schema.json):
        engine_version, rounding_decimals, comps_selection,
        provider_overrides, assumptions
    """
    filt = request.comps_selection.filters
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

    return {
        "engine_version": request.config.engine_version,
        "rounding_decimals": request.config.rounding_decimals,
        "comps_selection": {
            "universe": request.comps_selection.universe,
            "filters": filters_dict,
            "max_comps": request.comps_selection.max_comps,
            "sort_key": request.comps_selection.sort_key,
        },
        "provider_overrides": {
            "comps_provider": request.provider_overrides.comps_provider,
        },
        "assumptions": {
            "outlier_policy": request.assumptions.outlier_policy,
            "outlier_quantile": request.assumptions.outlier_quantile,
            "quantile_method": request.assumptions.quantile_method,
        },
    }


def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical JSON serialization for hashing.

    Rules: sort_keys=True, separators=(',', ':'), ensure_ascii=False, encode utf-8.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_hash(data: bytes) -> str:
    """Return 'sha256:<hex>' of the given bytes."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def compute_output_hash(report_dict: dict) -> str:
    """Compute the output_hash of a report dict.

    The report_dict MUST already be fully assembled.
    Before hashing, set report_dict["run_manifest"]["output_hash"] to ""
    so the hash is not self-referential.

    Returns:
        'sha256:<hex>'
    """
    # Zero out the output_hash field before hashing to avoid self-reference
    import copy
    d = copy.deepcopy(report_dict)
    d["run_manifest"]["output_hash"] = ""
    return compute_hash(canonical_json_bytes(d))
