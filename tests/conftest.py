"""Shared pytest fixtures for the valuation-audit-trail test suite.

Provides reusable factories for CompCandidate, CompsSelection, Assumptions,
and full request dicts so individual test files stay focused on assertions.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from valuation_audit_trail.models import (
    Assumptions,
    CompCandidate,
    CompsSelection,
    Filters,
    RevenueBand,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
SCHEMAS_DIR = REPO_ROOT / "schemas"
DATA_DIR = REPO_ROOT / "data"


# ---------------------------------------------------------------------------
# Candidate factory
# ---------------------------------------------------------------------------

def make_candidate(
    company_id: str = "C-001",
    ticker: str = "AAA",
    name: str = "Test Corp",
    ev: float = 1000.0,
    revenue_ltm: float = 100.0,
    sector: str = "Application Software",
    industry_tags: list[str] | None = None,
    geography: str = "US",
    size: str = "mid",
    universe: str = "global_software",
) -> CompCandidate:
    """Build a CompCandidate with sensible defaults for testing."""
    return CompCandidate(
        company_id=company_id,
        ticker=ticker,
        name=name,
        ev=ev,
        revenue_ltm=revenue_ltm,
        sector=sector,
        industry_tags=industry_tags or ["software"],
        geography=geography,
        size=size,
        universe=universe,
    )


# ---------------------------------------------------------------------------
# Selection / Assumptions factories
# ---------------------------------------------------------------------------

def make_filters(**overrides) -> Filters:
    """Build Filters with empty defaults (= no filtering) unless overridden."""
    defaults = dict(
        sector=[],
        size=[],
        industry_keywords=[],
        geographies=[],
        revenue_band=None,
    )
    defaults.update(overrides)
    return Filters(**defaults)


def make_selection(
    filters: Filters | None = None,
    universe: str = "global_software",
    max_comps: int = 20,
    sort_key: list[str] | None = None,
) -> CompsSelection:
    """Build a CompsSelection with safe defaults."""
    return CompsSelection(
        universe=universe,
        filters=filters or make_filters(),
        max_comps=max_comps,
        sort_key=sort_key or ["ticker", "company_id"],
    )


def make_assumptions(
    outlier_policy: str = "none",
    outlier_quantile: float = 0.1,
    quantile_method: str = "nearest_rank",
) -> Assumptions:
    """Build an Assumptions object."""
    return Assumptions(
        outlier_policy=outlier_policy,
        outlier_quantile=outlier_quantile,
        quantile_method=quantile_method,
    )


# ---------------------------------------------------------------------------
# Full request dict (matches input.schema.json)
# ---------------------------------------------------------------------------

@pytest.fixture()
def base_request_dict() -> dict:
    """Return a minimal valid request dict that passes schema validation.

    Tests can deep-copy and mutate specific fields as needed.
    """
    return {
        "request_id": "test-001",
        "as_of_date": "2026-01-01",
        "currency": "USD",
        "method": "comps_ev_revenue",
        "subject": {
            "company_id": "SUBJ-T",
            "company_name": "Test Subject Co",
            "sector": "Application Software",
            "revenue_ltm": 500.0,
        },
        "comps_selection": {
            "universe": "global_software",
            "filters": {
                "sector": ["Application Software"],
                "size": ["mid"],
                "industry_keywords": ["software"],
                "geographies": ["US"],
                "revenue_band": {"min": 100.0, "max": 1000.0},
            },
            "max_comps": 10,
            "sort_key": ["ticker", "company_id"],
        },
        "provider_overrides": {"comps_provider": "mock_v1"},
        "assumptions": {
            "outlier_policy": "none",
            "outlier_quantile": 0.1,
            "quantile_method": "nearest_rank",
        },
        "config": {"engine_version": "0.2.0", "rounding_decimals": 2},
    }


@pytest.fixture()
def example_request() -> dict:
    """Load the canonical examples/request.json."""
    return json.loads((EXAMPLES_DIR / "request.json").read_text())


@pytest.fixture()
def example_winsorize_request() -> dict:
    """Load examples/request_winsorize.json."""
    return json.loads((EXAMPLES_DIR / "request_winsorize.json").read_text())
