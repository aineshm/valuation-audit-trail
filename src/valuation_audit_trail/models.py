"""Data model boundary for request, report, provenance, and manifest structures.

All models are frozen dataclasses.  They carry a `.to_dict()` that produces the
exact JSON structure specified by the schemas.  No business logic lives here —
only structural mapping.

Convention: field names match JSON keys 1-to-1 (snake_case in Python, camelCase
is NOT used in our schemas so no translation is needed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Request-side models  (mirrors schemas/input.schema.json)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class Subject:
    """The company being valued."""
    company_id: str
    company_name: str
    sector: str
    revenue_ltm: float


@dataclass(frozen=True, slots=True)
class RevenueBand:
    min: float
    max: float


@dataclass(frozen=True, slots=True)
class Filters:
    sector: list[str] = field(default_factory=list)
    size: list[str] = field(default_factory=list)
    industry_keywords: list[str] = field(default_factory=list)
    geographies: list[str] = field(default_factory=list)
    revenue_band: RevenueBand | None = None


@dataclass(frozen=True, slots=True)
class CompsSelection:
    universe: str
    filters: Filters
    max_comps: int
    sort_key: list[str]


@dataclass(frozen=True, slots=True)
class ProviderOverrides:
    comps_provider: str  # currently always "mock_v1"


@dataclass(frozen=True, slots=True)
class Assumptions:
    outlier_policy: str  # "none" | "trim" | "winsorize"
    outlier_quantile: float
    quantile_method: str  # "nearest_rank" | "linear_interpolation"


@dataclass(frozen=True, slots=True)
class Config:
    engine_version: str
    rounding_decimals: int


@dataclass(frozen=True, slots=True)
class ValuationRequest:
    """Top-level request object. Constructed from a validated JSON dict."""
    request_id: str
    as_of_date: str
    currency: str
    method: str  # always "comps_ev_revenue" for now
    subject: Subject
    comps_selection: CompsSelection
    provider_overrides: ProviderOverrides
    assumptions: Assumptions
    config: Config

    @staticmethod
    def from_dict(d: dict) -> "ValuationRequest":
        """Parse a raw JSON dict into a typed ValuationRequest.

        This is the ONLY place where raw dict → dataclass conversion happens.
        Raises KeyError / TypeError on missing/invalid keys.
        """
        subj = d["subject"]
        subject = Subject(
            company_id=subj["company_id"],
            company_name=subj["company_name"],
            sector=subj["sector"],
            revenue_ltm=float(subj["revenue_ltm"]),
        )

        cs = d["comps_selection"]
        filt_raw = cs.get("filters", {})
        rb_raw = filt_raw.get("revenue_band")
        revenue_band = (
            RevenueBand(min=float(rb_raw["min"]), max=float(rb_raw["max"]))
            if rb_raw is not None
            else None
        )
        filters = Filters(
            sector=filt_raw.get("sector", []),
            size=filt_raw.get("size", []),
            industry_keywords=filt_raw.get("industry_keywords", []),
            geographies=filt_raw.get("geographies", []),
            revenue_band=revenue_band,
        )
        comps_selection = CompsSelection(
            universe=cs["universe"],
            filters=filters,
            max_comps=int(cs["max_comps"]),
            sort_key=cs["sort_key"],
        )

        po = d["provider_overrides"]
        provider_overrides = ProviderOverrides(comps_provider=po["comps_provider"])

        asm = d["assumptions"]
        assumptions = Assumptions(
            outlier_policy=asm["outlier_policy"],
            outlier_quantile=float(asm["outlier_quantile"]),
            quantile_method=asm["quantile_method"],
        )

        cfg = d["config"]
        config = Config(
            engine_version=cfg["engine_version"],
            rounding_decimals=int(cfg["rounding_decimals"]),
        )

        return ValuationRequest(
            request_id=d["request_id"],
            as_of_date=d["as_of_date"],
            currency=d["currency"],
            method=d["method"],
            subject=subject,
            comps_selection=comps_selection,
            provider_overrides=provider_overrides,
            assumptions=assumptions,
            config=config,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Provider-side models  (mirrors data/mock_comps_v1.json per-comp entry)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class CompCandidate:
    """A single comparable company as loaded from a provider dataset.

    The ``universe`` field is stamped by the provider at load time so the
    valuation layer can filter on it without any knowledge of the
    underlying data source format.
    """
    company_id: str
    ticker: str
    name: str
    ev: float
    revenue_ltm: float
    sector: str
    industry_tags: list[str]
    geography: str
    size: str       # "small" | "mid" | "large"
    universe: str   # e.g. "global_software" — set by the provider


# ═══════════════════════════════════════════════════════════════════════════
# Report-side models  (mirrors schemas/report.schema.json)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class FairValue:
    currency: str
    point: float
    range_low: float
    range_high: float
    range_basis: str  # always "q25_q75"
    provenance_node_ids: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "currency": self.currency,
            "point": self.point,
            "range": {"low": self.range_low, "high": self.range_high},
            "range_basis": self.range_basis,
            "provenance_node_ids": self.provenance_node_ids,
        }


@dataclass(frozen=True, slots=True)
class Valuation:
    method: str  # "comps_ev_revenue"
    fair_value: FairValue

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "fair_value": self.fair_value.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AssumptionEntry:
    """report.schema.json → $defs/assumption_entry"""
    id: str
    name: str
    value: Any

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """report.schema.json → $defs/source_entry"""
    id: str
    provider: str
    dataset: str
    dataset_version: str
    dataset_hash: str
    citation: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "dataset_hash": self.dataset_hash,
            "citation": self.citation,
        }


@dataclass(frozen=True, slots=True)
class MatchedFilters:
    """Per-filter booleans for one candidate."""
    universe: bool
    sector: bool
    size: bool
    industry_keywords: bool
    geographies: bool
    revenue_band: bool
    ev_positive: bool

    def to_dict(self) -> dict:
        return {
            "universe": self.universe,
            "sector": self.sector,
            "size": self.size,
            "industry_keywords": self.industry_keywords,
            "geographies": self.geographies,
            "revenue_band": self.revenue_band,
            "ev_positive": self.ev_positive,
        }


@dataclass(frozen=True, slots=True)
class MatchDetailEntry:
    """report.schema.json → $defs/match_detail_entry"""
    company_id: str
    ticker: str
    included: bool
    matched_filters: MatchedFilters
    excluded_reason: str | None  # null when included=True
    selection_rank: int | None  # null when filtered out before ranking

    def to_dict(self) -> dict:
        return {
            "company_id": self.company_id,
            "ticker": self.ticker,
            "included": self.included,
            "matched_filters": self.matched_filters.to_dict(),
            "excluded_reason": self.excluded_reason,
            "selection_rank": self.selection_rank,
        }


@dataclass(frozen=True, slots=True)
class CompsSelectionResult:
    """report.schema.json → $defs/comps_selection_result"""
    requested: dict  # echoed comps_selection from request (raw dict)
    dataset_universe: str
    sort_key_used: list[str]
    matched_before_limit: int
    included_count: int
    included_company_ids: list[str]
    match_details: list[MatchDetailEntry]

    def to_dict(self) -> dict:
        return {
            "requested": self.requested,
            "dataset_universe": self.dataset_universe,
            "sort_key_used": self.sort_key_used,
            "matched_before_limit": self.matched_before_limit,
            "included_count": self.included_count,
            "included_company_ids": self.included_company_ids,
            "match_details": [m.to_dict() for m in self.match_details],
        }


@dataclass(frozen=True, slots=True)
class ProvenanceNode:
    """report.schema.json → $defs/provenance_node"""
    id: str
    field_path: str
    formula: str
    inputs: dict
    assumption_ids: list[str]
    source_ids: list[str]
    output: Any
    parent_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "field_path": self.field_path,
            "formula": self.formula,
            "inputs": self.inputs,
            "assumption_ids": self.assumption_ids,
            "source_ids": self.source_ids,
            "output": self.output,
        }
        if self.parent_ids:
            d["parent_ids"] = self.parent_ids
        return d


@dataclass(frozen=True, slots=True)
class ProviderFingerprint:
    provider: str
    dataset: str
    version: str
    hash: str

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "dataset": self.dataset,
            "version": self.version,
            "hash": self.hash,
        }


@dataclass(frozen=True, slots=True)
class RunManifest:
    """report.schema.json → $defs/run_manifest"""
    timestamp_utc: str
    engine_version: str
    config_snapshot: dict
    input_hash: str
    provider_fingerprints: list[ProviderFingerprint]
    output_hash: str

    def to_dict(self) -> dict:
        return {
            "timestamp_utc": self.timestamp_utc,
            "engine_version": self.engine_version,
            "config_snapshot": self.config_snapshot,
            "input_hash": self.input_hash,
            "provider_fingerprints": [fp.to_dict() for fp in self.provider_fingerprints],
            "output_hash": self.output_hash,
        }


@dataclass(slots=True)
class ValuationReport:
    """Top-level report object.  Mutable only for the output_hash fixup.

    Build order:
        1. Create with valuation, key_inputs, comps_selection_result, provenance nodes
        2. Call manifest.build_manifest() to populate run_manifest
        3. Serialize via to_dict() — this must produce a dict that validates
           against schemas/report.schema.json
    """
    request_id: str
    as_of_date: str
    currency: str
    status: str  # "ok" | "error"
    valuation: Valuation | None
    key_inputs: dict
    assumptions_used: list[AssumptionEntry]
    sources: list[SourceEntry]
    comps_selection_result: CompsSelectionResult
    provenance_nodes: list[ProvenanceNode]
    run_manifest: RunManifest | None
    warnings: list  # list[Issue.to_dict()]
    errors: list  # list[Issue.to_dict()]

    def to_dict(self) -> dict:
        """Serialize to the full JSON-schema-aligned report dict."""
        return {
            "request_id": self.request_id,
            "as_of_date": self.as_of_date,
            "currency": self.currency,
            "status": self.status,
            "valuation": self.valuation.to_dict() if self.valuation else None,
            "key_inputs": self.key_inputs,
            "assumptions_used": [a.to_dict() for a in self.assumptions_used],
            "sources": [s.to_dict() for s in self.sources],
            "comps_selection_result": self.comps_selection_result.to_dict(),
            "provenance": {"nodes": [n.to_dict() for n in self.provenance_nodes]},
            "run_manifest": self.run_manifest.to_dict() if self.run_manifest else {},
            "warnings": self.warnings,
            "errors": self.errors,
        }
