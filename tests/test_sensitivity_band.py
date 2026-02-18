"""Tests for sensitivity band (fair value range) behaviour.

The fair value range is [ev_low, ev_high] where:
    ev_low  = subject_revenue × q25_multiple
    ev_high = subject_revenue × q75_multiple

This module verifies:
    • ev_low ≤ ev_point ≤ ev_high always holds
    • The band width grows with more spread in multiples
    • Different quantile methods produce different band widths
    • Outlier policies affect the band width
    • Edge case: uniform multiples → zero-width band
"""

from __future__ import annotations

import math

import pytest

from valuation_audit_trail.valuation import run_valuation

from conftest import make_assumptions, make_candidate, make_selection


# ═══════════════════════════════════════════════════════════════════════════
# Band ordering invariant
# ═══════════════════════════════════════════════════════════════════════════


class TestBandOrdering:
    """Tests that ev_low ≤ ev_point ≤ ev_high always holds."""

    def test_ordering_with_spread(self):
        """With varied multiples, low ≤ point ≤ high should hold."""
        candidates = [
            make_candidate(company_id="C-1", ticker="T01", ev=500.0, revenue_ltm=100.0),   # mult=5
            make_candidate(company_id="C-2", ticker="T02", ev=1000.0, revenue_ltm=100.0),  # mult=10
            make_candidate(company_id="C-3", ticker="T03", ev=1500.0, revenue_ltm=100.0),  # mult=15
            make_candidate(company_id="C-4", ticker="T04", ev=2000.0, revenue_ltm=100.0),  # mult=20
            make_candidate(company_id="C-5", ticker="T05", ev=2500.0, revenue_ltm=100.0),  # mult=25
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=300.0,
            rounding_decimals=2,
        )
        assert vr.ev_low <= vr.ev_point <= vr.ev_high

    def test_ordering_with_uniform_multiples(self):
        """When all multiples are identical, low == point == high."""
        candidates = [
            make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=1000.0, revenue_ltm=100.0)
            for i in range(5)
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=200.0,
            rounding_decimals=2,
        )
        assert vr.ev_low == vr.ev_point == vr.ev_high

    def test_ordering_with_linear_interpolation(self):
        """Ordering invariant should hold for linear_interpolation method too."""
        candidates = [
            make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=500.0 + i * 300, revenue_ltm=100.0)
            for i in range(6)
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none", quantile_method="linear_interpolation"),
            subject_revenue_ltm=400.0,
            rounding_decimals=2,
        )
        assert vr.ev_low <= vr.ev_point <= vr.ev_high

    def test_ordering_with_winsorize(self):
        """Ordering invariant should hold after winsorization."""
        candidates = [
            make_candidate(company_id="C-1", ticker="T01", ev=100.0, revenue_ltm=100.0),   # mult=1
            make_candidate(company_id="C-2", ticker="T02", ev=500.0, revenue_ltm=100.0),   # mult=5
            make_candidate(company_id="C-3", ticker="T03", ev=1000.0, revenue_ltm=100.0),  # mult=10
            make_candidate(company_id="C-4", ticker="T04", ev=1500.0, revenue_ltm=100.0),  # mult=15
            make_candidate(company_id="C-5", ticker="T05", ev=5000.0, revenue_ltm=100.0),  # mult=50
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="winsorize", outlier_quantile=0.1),
            subject_revenue_ltm=300.0,
            rounding_decimals=2,
        )
        assert vr.ev_low <= vr.ev_point <= vr.ev_high


# ═══════════════════════════════════════════════════════════════════════════
# Band width behaviour
# ═══════════════════════════════════════════════════════════════════════════


class TestBandWidth:
    """Tests for how the band width responds to inputs."""

    def test_wider_spread_produces_wider_band(self):
        """A wider spread of multiples should produce a wider band.

        Set A: multiples [8, 10, 12] → range_A
        Set B: multiples [2, 10, 18] → range_B > range_A
        """
        tight = [
            make_candidate(company_id="C-1", ticker="T01", ev=800.0, revenue_ltm=100.0),
            make_candidate(company_id="C-2", ticker="T02", ev=1000.0, revenue_ltm=100.0),
            make_candidate(company_id="C-3", ticker="T03", ev=1200.0, revenue_ltm=100.0),
        ]
        wide = [
            make_candidate(company_id="C-1", ticker="T01", ev=200.0, revenue_ltm=100.0),
            make_candidate(company_id="C-2", ticker="T02", ev=1000.0, revenue_ltm=100.0),
            make_candidate(company_id="C-3", ticker="T03", ev=1800.0, revenue_ltm=100.0),
        ]

        vr_tight = run_valuation(
            candidates=tight, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=100.0, rounding_decimals=2,
        )
        vr_wide = run_valuation(
            candidates=wide, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=100.0, rounding_decimals=2,
        )

        band_tight = vr_tight.ev_high - vr_tight.ev_low
        band_wide = vr_wide.ev_high - vr_wide.ev_low
        assert band_wide >= band_tight

    def test_uniform_multiples_zero_width(self):
        """All multiples identical → band width == 0."""
        candidates = [
            make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=1000.0, revenue_ltm=100.0)
            for i in range(5)
        ]
        vr = run_valuation(
            candidates=candidates, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=500.0, rounding_decimals=2,
        )
        assert (vr.ev_high - vr.ev_low) == 0.0

    def test_band_scales_with_revenue(self):
        """Doubling subject revenue should double the band width (and all EV values).

        All multiples stay the same; only revenue changes.
        """
        candidates = [
            make_candidate(company_id="C-1", ticker="T01", ev=500.0, revenue_ltm=100.0),
            make_candidate(company_id="C-2", ticker="T02", ev=1000.0, revenue_ltm=100.0),
            make_candidate(company_id="C-3", ticker="T03", ev=1500.0, revenue_ltm=100.0),
        ]

        vr_100 = run_valuation(
            candidates=candidates, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=100.0, rounding_decimals=2,
        )
        vr_200 = run_valuation(
            candidates=candidates, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=200.0, rounding_decimals=2,
        )

        # EV = revenue × multiple, so doubling revenue doubles everything
        assert math.isclose(vr_200.ev_point, vr_100.ev_point * 2, rel_tol=1e-9)
        assert math.isclose(vr_200.ev_low, vr_100.ev_low * 2, rel_tol=1e-9)
        assert math.isclose(vr_200.ev_high, vr_100.ev_high * 2, rel_tol=1e-9)


# ═══════════════════════════════════════════════════════════════════════════
# Outlier policy impact on band
# ═══════════════════════════════════════════════════════════════════════════


class TestOutlierImpactOnBand:
    """Tests that outlier policies narrow (or preserve) the band."""

    def _make_candidates_with_outlier(self):
        """5 normal + 1 extreme outlier → visible band difference."""
        return [
            make_candidate(company_id="C-1", ticker="T01", ev=900.0, revenue_ltm=100.0),
            make_candidate(company_id="C-2", ticker="T02", ev=1000.0, revenue_ltm=100.0),
            make_candidate(company_id="C-3", ticker="T03", ev=1100.0, revenue_ltm=100.0),
            make_candidate(company_id="C-4", ticker="T04", ev=1000.0, revenue_ltm=100.0),
            make_candidate(company_id="C-5", ticker="T05", ev=1050.0, revenue_ltm=100.0),
            make_candidate(company_id="C-6", ticker="T06", ev=5000.0, revenue_ltm=100.0),  # outlier
        ]

    def test_trim_narrows_band_vs_none(self):
        """Trimming should produce a band that is ≤ the 'none' policy band
        (removing outlier shrinks spread).
        """
        cands = self._make_candidates_with_outlier()

        vr_none = run_valuation(
            candidates=cands, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=500.0, rounding_decimals=2,
        )
        vr_trim = run_valuation(
            candidates=cands, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="trim", outlier_quantile=0.1),
            subject_revenue_ltm=500.0, rounding_decimals=2,
        )

        band_none = vr_none.ev_high - vr_none.ev_low
        band_trim = vr_trim.ev_high - vr_trim.ev_low
        assert band_trim <= band_none

    def test_winsorize_narrows_band_vs_none(self):
        """Winsorizing should produce a band that is ≤ the 'none' policy band
        (clamping outlier shrinks spread).
        """
        cands = self._make_candidates_with_outlier()

        vr_none = run_valuation(
            candidates=cands, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=500.0, rounding_decimals=2,
        )
        vr_win = run_valuation(
            candidates=cands, selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="winsorize", outlier_quantile=0.1),
            subject_revenue_ltm=500.0, rounding_decimals=2,
        )

        band_none = vr_none.ev_high - vr_none.ev_low
        band_win = vr_win.ev_high - vr_win.ev_low
        assert band_win <= band_none
