"""Tests for error / validation paths.

Covers:
    • validate_request — invalid revenue, invalid revenue band
    • validate_comps_count — 0, 1-2, 3-5, >5
    • run_valuation when all candidates are filtered out (E_NO_COMPS)
    • run_valuation when fewer than MIN_COMPS remain (E_TOO_FEW_COMPS)
    • Boundary values at the MIN_COMPS and LOW_COMPS_THRESHOLD thresholds
"""

from __future__ import annotations

import pytest

from valuation_audit_trail.errors import (
    validate_request,
    validate_comps_count,
    MIN_COMPS,
    LOW_COMPS_THRESHOLD,
)
from valuation_audit_trail.valuation import run_valuation

from conftest import make_assumptions, make_candidate, make_filters, make_selection


# ═══════════════════════════════════════════════════════════════════════════
# validate_request
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateRequest:
    """Tests for schema-level request validation."""

    def test_valid_request_returns_no_issues(self, base_request_dict):
        """A fully-valid request should produce zero errors and zero warnings."""
        errors, warnings = validate_request(base_request_dict)
        assert errors == []
        assert warnings == []

    def test_zero_revenue_is_error(self, base_request_dict):
        """Subject revenue_ltm = 0 should yield E_REVENUE_NOT_POSITIVE."""
        base_request_dict["subject"]["revenue_ltm"] = 0.0
        errors, _ = validate_request(base_request_dict)
        assert any(e.code == "E_REVENUE_NOT_POSITIVE" for e in errors)

    def test_negative_revenue_is_error(self, base_request_dict):
        """Subject revenue_ltm < 0 should yield E_REVENUE_NOT_POSITIVE."""
        base_request_dict["subject"]["revenue_ltm"] = -100.0
        errors, _ = validate_request(base_request_dict)
        assert any(e.code == "E_REVENUE_NOT_POSITIVE" for e in errors)

    def test_inverted_revenue_band_is_error(self, base_request_dict):
        """Revenue band with min > max should yield E_INVALID_REVENUE_BAND."""
        base_request_dict["comps_selection"]["filters"]["revenue_band"] = {"min": 500.0, "max": 100.0}
        errors, _ = validate_request(base_request_dict)
        assert any(e.code == "E_INVALID_REVENUE_BAND" for e in errors)

    def test_equal_revenue_band_is_valid(self, base_request_dict):
        """Revenue band with min == max is valid (degenerate but legal)."""
        base_request_dict["comps_selection"]["filters"]["revenue_band"] = {"min": 200.0, "max": 200.0}
        errors, _ = validate_request(base_request_dict)
        assert errors == []

    def test_missing_revenue_band_is_valid(self, base_request_dict):
        """No revenue_band at all should not be an error (filtering is optional)."""
        base_request_dict["comps_selection"]["filters"].pop("revenue_band", None)
        errors, _ = validate_request(base_request_dict)
        assert errors == []


# ═══════════════════════════════════════════════════════════════════════════
# validate_comps_count
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateCompsCount:
    """Tests for post-filter comp-count validation."""

    def test_zero_comps_is_fatal(self):
        """0 included comps → E_NO_COMPS error."""
        errors, warnings = validate_comps_count(0)
        assert any(e.code == "E_NO_COMPS" for e in errors)
        assert warnings == []

    def test_one_comp_is_too_few(self):
        """1 comp (< MIN_COMPS=3) → E_TOO_FEW_COMPS error."""
        errors, warnings = validate_comps_count(1)
        assert any(e.code == "E_TOO_FEW_COMPS" for e in errors)

    def test_two_comps_is_too_few(self):
        """2 comps (< MIN_COMPS=3) → E_TOO_FEW_COMPS error."""
        errors, warnings = validate_comps_count(2)
        assert any(e.code == "E_TOO_FEW_COMPS" for e in errors)

    def test_exactly_min_comps_yields_warning(self):
        """Exactly MIN_COMPS (3) comps → warning W_LOW_COMP_COUNT."""
        errors, warnings = validate_comps_count(MIN_COMPS)
        assert errors == []
        assert any(w.code == "W_LOW_COMP_COUNT" for w in warnings)

    def test_at_low_threshold_yields_warning(self):
        """Exactly LOW_COMPS_THRESHOLD (5) comps → W_LOW_COMP_COUNT warning."""
        errors, warnings = validate_comps_count(LOW_COMPS_THRESHOLD)
        assert errors == []
        assert any(w.code == "W_LOW_COMP_COUNT" for w in warnings)

    def test_above_threshold_is_clean(self):
        """6 comps (> LOW_COMPS_THRESHOLD=5) → no errors, no warnings."""
        errors, warnings = validate_comps_count(LOW_COMPS_THRESHOLD + 1)
        assert errors == []
        assert warnings == []

    def test_large_count_is_clean(self):
        """50 comps → should still be clean (no upper limit on comps)."""
        errors, warnings = validate_comps_count(50)
        assert errors == []
        assert warnings == []


# ═══════════════════════════════════════════════════════════════════════════
# run_valuation error paths
# ═══════════════════════════════════════════════════════════════════════════


class TestValuationErrorPaths:
    """Tests for run_valuation when the comp set is too small."""

    def test_all_excluded_yields_no_comps_error(self):
        """If every candidate fails filters, run_valuation should return an
        E_NO_COMPS error and zero-out fair value fields (ev_point=0.0).
        """
        # All candidates have ev=0 → filter_ev_not_positive
        candidates = [make_candidate(company_id=f"C-{i}", ticker=f"T{i}", ev=0.0) for i in range(5)]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert any(e.code == "E_NO_COMPS" for e in vr.errors)
        assert vr.ev_point == 0.0

    def test_two_included_yields_too_few_error(self):
        """With only 2 passing candidates (< MIN_COMPS=3), run_valuation should
        return E_TOO_FEW_COMPS and zero-out fair value fields.
        """
        good = [make_candidate(company_id=f"C-{i}", ticker=f"T{i}") for i in range(2)]
        bad = [make_candidate(company_id="C-bad", ticker="BAD", ev=0.0)]
        vr = run_valuation(
            candidates=good + bad,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert any(e.code == "E_TOO_FEW_COMPS" for e in vr.errors)
        assert vr.ev_point == 0.0

    def test_error_report_includes_match_details(self):
        """Even when valuation errors out, match_details should still be populated
        so the user can see why each candidate was excluded.
        """
        candidates = [make_candidate(company_id=f"C-{i}", ticker=f"T{i}", ev=0.0) for i in range(3)]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert len(vr.match_details) == 3
        assert all(not d.included for d in vr.match_details)
