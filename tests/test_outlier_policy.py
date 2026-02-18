"""Tests for outlier policy behaviour (none / trim / winsorize).

Covers:
    • 'none' passes through untouched
    • 'trim' removes bottom-q and top-q values
    • 'winsorize' clamps extremes to the q-th and (1-q)-th quantile values
    • Both quantile methods (nearest_rank, linear_interpolation)
    • Edge case: all identical values
    • Edge case: single-value list after filter
    • Quantile math validation for specific examples
"""

from __future__ import annotations

import math

import pytest

from valuation_audit_trail.valuation import (
    _apply_outlier_policy,
    _quantile_nearest_rank,
    _quantile_linear_interpolation,
    _compute_quantile,
    run_valuation,
)

from conftest import make_assumptions, make_candidate, make_selection


# ═══════════════════════════════════════════════════════════════════════════
# _quantile_nearest_rank
# ═══════════════════════════════════════════════════════════════════════════


class TestQuantileNearestRank:
    """Tests for ceil(q*n)-1 nearest-rank quantile."""

    def test_median_of_three(self):
        """Sorted [1,2,3], q=0.5: ceil(0.5*3)=2 → index 1 → 2."""
        assert _quantile_nearest_rank([1, 2, 3], 0.5) == 2

    def test_q25_of_four(self):
        """Sorted [10,20,30,40], q=0.25: ceil(0.25*4)=1 → index 0 → 10."""
        assert _quantile_nearest_rank([10, 20, 30, 40], 0.25) == 10

    def test_q75_of_four(self):
        """Sorted [10,20,30,40], q=0.75: ceil(0.75*4)=3 → index 2 → 30."""
        assert _quantile_nearest_rank([10, 20, 30, 40], 0.75) == 30

    def test_q50_single_element(self):
        """Sorted [42], q=0.5: ceil(0.5*1)=1 → index 0 → 42."""
        assert _quantile_nearest_rank([42], 0.5) == 42

    def test_empty_raises_value_error(self):
        """Empty list should raise ValueError."""
        with pytest.raises(ValueError):
            _quantile_nearest_rank([], 0.5)

    def test_q0_returns_minimum(self):
        """q=0.01 on [1,2,3]: ceil(0.01*3)=1 → index 0 → 1 (≈min)."""
        assert _quantile_nearest_rank([1, 2, 3], 0.01) == 1

    def test_q1_returns_maximum(self):
        """q=1.0 on [1,2,3]: ceil(1.0*3)=3 → index 2 → 3 (=max)."""
        assert _quantile_nearest_rank([1, 2, 3], 1.0) == 3


# ═══════════════════════════════════════════════════════════════════════════
# _quantile_linear_interpolation
# ═══════════════════════════════════════════════════════════════════════════


class TestQuantileLinearInterpolation:
    """Tests for numpy-style linear interpolation quantile."""

    def test_median_of_three(self):
        """Sorted [1,2,3], q=0.5: virtual_index = 0.5*(3-1) = 1.0 → exact → 2.0."""
        result = _quantile_linear_interpolation([1, 2, 3], 0.5)
        assert math.isclose(result, 2.0)

    def test_q25_of_four(self):
        """Sorted [10,20,30,40], q=0.25: vi = 0.25*3 = 0.75.
        floor=0, frac=0.75 → 10 + 0.75*(20-10) = 17.5.
        """
        result = _quantile_linear_interpolation([10, 20, 30, 40], 0.25)
        assert math.isclose(result, 17.5)

    def test_q75_of_four(self):
        """Sorted [10,20,30,40], q=0.75: vi = 0.75*3 = 2.25.
        floor=2, frac=0.25 → 30 + 0.25*(40-30) = 32.5.
        """
        result = _quantile_linear_interpolation([10, 20, 30, 40], 0.75)
        assert math.isclose(result, 32.5)

    def test_single_element(self):
        """Single element [42], q=0.5: vi = 0.5*0 = 0.0 → 42.0."""
        result = _quantile_linear_interpolation([42], 0.5)
        assert math.isclose(result, 42.0)

    def test_empty_raises_value_error(self):
        """Empty list should raise ValueError."""
        with pytest.raises(ValueError):
            _quantile_linear_interpolation([], 0.5)


# ═══════════════════════════════════════════════════════════════════════════
# _compute_quantile dispatch
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeQuantileDispatch:
    """Tests for the dispatcher that routes to the correct quantile algorithm."""

    def test_nearest_rank_dispatch(self):
        """'nearest_rank' should delegate to the nearest-rank function."""
        val = _compute_quantile([1, 2, 3, 4, 5], 0.5, method="nearest_rank")
        expected = _quantile_nearest_rank([1, 2, 3, 4, 5], 0.5)
        assert val == expected

    def test_linear_interpolation_dispatch(self):
        """'linear_interpolation' should delegate to the linear-interpolation function."""
        val = _compute_quantile([1, 2, 3, 4, 5], 0.5, method="linear_interpolation")
        expected = _quantile_linear_interpolation([1, 2, 3, 4, 5], 0.5)
        assert math.isclose(val, expected)

    def test_unknown_method_raises(self):
        """An unrecognised method name should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown quantile_method"):
            _compute_quantile([1, 2, 3], 0.5, method="percentile_exclusive")


# ═══════════════════════════════════════════════════════════════════════════
# _apply_outlier_policy
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyOutlierPolicy:
    """Tests for the three outlier policies applied to sorted multiples.

    _apply_outlier_policy(sorted_multiples, policy, quantile, quantile_method)
    """

    SORTED = [2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]

    def test_none_policy_returns_unchanged(self):
        """Policy 'none' should return the input unchanged."""
        result = _apply_outlier_policy(
            list(self.SORTED), "none", 0.1, "nearest_rank",
        )
        assert result == self.SORTED

    def test_trim_removes_extremes(self):
        """Policy 'trim' with q=0.1 on 10 values:
        q10 = nearest_rank(0.1): ceil(0.1*10)=1 → index 0 → 2.0
        q90 = nearest_rank(0.9): ceil(0.9*10)=9 → index 8 → 18.0
        Values outside [2.0, 18.0] are removed.
        20.0 > 18.0, so it's removed. Everything from 2..18 survives.
        """
        result = _apply_outlier_policy(
            list(self.SORTED), "trim", 0.1, "nearest_rank",
        )
        assert all(2.0 <= v <= 18.0 for v in result)
        assert 20.0 not in result

    def test_winsorize_clamps_extremes(self):
        """Policy 'winsorize' with q=0.1 on 10 values:
        q10 = 2.0, q90 = 18.0.
        Values below q10 are clamped to q10, above q90 to q90.
        Result length should equal input length.
        """
        result = _apply_outlier_policy(
            list(self.SORTED), "winsorize", 0.1, "nearest_rank",
        )
        assert len(result) == len(self.SORTED)
        assert min(result) >= 2.0
        assert max(result) <= 18.0

    def test_winsorize_preserves_length(self):
        """Winsorize should never change the length of the list."""
        data = [1.0, 2.0, 3.0, 100.0, 200.0]
        result = _apply_outlier_policy(
            data, "winsorize", 0.2, "nearest_rank",
        )
        assert len(result) == 5

    def test_trim_can_reduce_length(self):
        """Trim should reduce the list length when extremes are present."""
        data = [1.0, 5.0, 6.0, 7.0, 100.0]
        result = _apply_outlier_policy(
            data, "trim", 0.2, "nearest_rank",
        )
        assert len(result) < 5


# ═══════════════════════════════════════════════════════════════════════════
# All-identical values edge case
# ═══════════════════════════════════════════════════════════════════════════


class TestAllIdenticalValues:
    """Edge case: all multiples are the same value."""

    def test_identical_values_no_outlier(self):
        """With all multiples = 10.0, quantiles should all be 10.0 regardless of method."""
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
        assert vr.multiple_low == 10.0
        assert vr.median_multiple == 10.0
        assert vr.multiple_high == 10.0
        assert vr.ev_point == 2000.0  # 200 * 10

    def test_identical_values_trim(self):
        """Trimming identical values should leave the same set (nothing is an outlier)."""
        candidates = [
            make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=500.0, revenue_ltm=50.0)
            for i in range(5)
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="trim", outlier_quantile=0.1),
            subject_revenue_ltm=100.0,
            rounding_decimals=2,
        )
        # All multiples = 10.0, no trimming possible
        assert vr.median_multiple == 10.0

    def test_identical_values_winsorize(self):
        """Winsorizing identical values should be a no-op."""
        candidates = [
            make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=500.0, revenue_ltm=50.0)
            for i in range(5)
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="winsorize", outlier_quantile=0.2),
            subject_revenue_ltm=100.0,
            rounding_decimals=2,
        )
        assert vr.adjusted_multiples == [10.0] * 5


# ═══════════════════════════════════════════════════════════════════════════
# Linear interpolation vs. nearest-rank divergence
# ═══════════════════════════════════════════════════════════════════════════


class TestMethodDivergence:
    """Tests showing that the two quantile methods can produce different results."""

    def test_methods_diverge_on_even_count(self):
        """With 4 sorted values [8,10,12,14]:
        nearest_rank q50: ceil(0.5*4)=2 → index 1 → 10
        linear_interp q50: vi=0.5*3=1.5 → 10 + 0.5*(12-10) = 11.0
        """
        sorted_vals = [8.0, 10.0, 12.0, 14.0]
        nr = _quantile_nearest_rank(sorted_vals, 0.5)
        li = _quantile_linear_interpolation(sorted_vals, 0.5)
        assert nr == 10.0
        assert math.isclose(li, 11.0)
        assert nr != li
