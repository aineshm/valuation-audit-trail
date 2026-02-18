"""Tests for the happy-path workflow — a successful valuation from end to end.

Covers:
    • Full pipeline from request → report via run_valuation()
    • Correct comp filtering with sector/size/geo/revenue_band
    • Deterministic sort order via sort_key
    • Raw multiples computation (EV / revenue_ltm)
    • Quantile extraction (q25, q50, q75 via nearest_rank)
    • Fair-value calculation (revenue × multiple)
    • Report structure matches schema expectations
    • CLI integration with example request.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from valuation_audit_trail.models import ValuationRequest, RevenueBand
from valuation_audit_trail.valuation import run_valuation

from conftest import (
    EXAMPLES_DIR,
    REPO_ROOT,
    SCHEMAS_DIR,
    make_assumptions,
    make_candidate,
    make_filters,
    make_selection,
)


# ═══════════════════════════════════════════════════════════════════════════
# Unit tests: run_valuation() directly
# ═══════════════════════════════════════════════════════════════════════════


class TestRunValuationHappyPath:
    """Tests for the core valuation pipeline with controlled inputs."""

    # Three candidates that all pass filters, with known multiples.
    # ev / revenue_ltm → 10.0, 12.0, 8.0
    CANDIDATES = [
        make_candidate(company_id="C-A", ticker="AAA", ev=1000.0, revenue_ltm=100.0),
        make_candidate(company_id="C-B", ticker="BBB", ev=1200.0, revenue_ltm=100.0),
        make_candidate(company_id="C-C", ticker="CCC", ev=800.0, revenue_ltm=100.0),
    ]

    def test_included_count_matches_candidates(self):
        """All three candidates pass open filters → all three should be included."""
        vr = run_valuation(
            candidates=self.CANDIDATES,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert len(vr.included_candidates) == 3
        assert vr.errors == []

    def test_raw_multiples_order_follows_selection_order(self):
        """Raw multiples should be in the deterministic sort order (AAA, BBB, CCC).

        After sorting by ticker: AAA(10.0), BBB(12.0), CCC(8.0).
        """
        vr = run_valuation(
            candidates=self.CANDIDATES,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert vr.raw_multiples == [10.0, 12.0, 8.0]

    def test_adjusted_multiples_sorted_no_outlier(self):
        """With outlier_policy='none', adjusted multiples should be the sorted raw multiples."""
        vr = run_valuation(
            candidates=self.CANDIDATES,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        # Sorted: [8.0, 10.0, 12.0]
        assert vr.adjusted_multiples == [8.0, 10.0, 12.0]

    def test_quantiles_nearest_rank_three_values(self):
        """With 3 sorted values [8.0, 10.0, 12.0] and nearest_rank:

        q25: ceil(0.25 * 3) = 1 → index 0 → 8.0
        q50: ceil(0.50 * 3) = 2 → index 1 → 10.0
        q75: ceil(0.75 * 3) = 3 → index 2 → 12.0
        """
        vr = run_valuation(
            candidates=self.CANDIDATES,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none", quantile_method="nearest_rank"),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert vr.multiple_low == 8.0
        assert vr.median_multiple == 10.0
        assert vr.multiple_high == 12.0

    def test_fair_value_calculation(self):
        """EV = subject_revenue_ltm × multiple.

        point = 500 × 10.0 = 5000.0
        low   = 500 × 8.0  = 4000.0
        high  = 500 × 12.0 = 6000.0
        """
        vr = run_valuation(
            candidates=self.CANDIDATES,
            selection=make_selection(),
            assumptions=make_assumptions(outlier_policy="none"),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert vr.ev_point == 5000.0
        assert vr.ev_low == 4000.0
        assert vr.ev_high == 6000.0

    def test_low_comp_count_warning_emitted(self):
        """With exactly 3 comps (≤ LOW_COMPS_THRESHOLD=5, ≥ MIN_COMPS=3),
        a W_LOW_COMP_COUNT warning should be emitted but valuation should succeed.
        """
        vr = run_valuation(
            candidates=self.CANDIDATES,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert vr.errors == []
        assert len(vr.warnings) == 1
        assert vr.warnings[0].code == "W_LOW_COMP_COUNT"

    def test_no_warning_when_above_threshold(self):
        """With 6 comps (> LOW_COMPS_THRESHOLD=5), no warning should be emitted."""
        candidates = [
            make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=1000.0, revenue_ltm=100.0)
            for i in range(6)
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert vr.errors == []
        assert vr.warnings == []

    def test_match_details_include_all_candidates(self):
        """match_details should contain one entry per candidate, whether included or not."""
        # Add a candidate that fails sector filter
        extra = make_candidate(company_id="C-X", ticker="XXX", sector="Wrong Sector")
        candidates = self.CANDIDATES + [extra]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(filters=make_filters(sector=["Application Software"])),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert len(vr.match_details) == 4
        excluded = [d for d in vr.match_details if not d.included]
        assert len(excluded) == 1
        assert excluded[0].excluded_reason == "filter_sector"

    def test_selection_rank_is_one_based(self):
        """Included candidates should have selection_rank starting at 1."""
        vr = run_valuation(
            candidates=self.CANDIDATES,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        included = [d for d in vr.match_details if d.included]
        ranks = [d.selection_rank for d in included]
        assert ranks == [1, 2, 3]


# ═══════════════════════════════════════════════════════════════════════════
# Filtering unit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFiltering:
    """Tests for individual filter dimensions in the valuation pipeline."""

    def _run(self, candidates, filters, **kwargs):
        """Helper to run valuation with given candidates and filters."""
        defaults = dict(
            selection=make_selection(filters=filters),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        defaults.update(kwargs)
        return run_valuation(candidates=candidates, **defaults)

    def test_sector_filter_case_insensitive(self):
        """Sector filter should match case-insensitively.

        Candidate has 'Application Software', filter asks for 'application software'.
        Expected: candidate passes.
        """
        cand = make_candidate(sector="Application Software")
        vr = self._run(
            [cand] * 3,  # need 3 to pass MIN_COMPS
            make_filters(sector=["application software"]),
        )
        assert len(vr.included_candidates) == 3

    def test_sector_filter_rejects_mismatch(self):
        """Candidate with sector 'Infrastructure Software' should fail
        a filter requiring 'Application Software'.
        """
        cand = make_candidate(sector="Infrastructure Software")
        vr = self._run([cand], make_filters(sector=["Application Software"]))
        assert len(vr.included_candidates) == 0

    def test_size_filter_accepts_matching_bucket(self):
        """A 'mid' candidate should pass a filter requesting ['mid', 'large']."""
        cand = make_candidate(size="mid")
        vr = self._run([cand] * 3, make_filters(size=["mid", "large"]))
        assert len(vr.included_candidates) == 3

    def test_size_filter_rejects_non_matching(self):
        """A 'small' candidate should fail a filter requesting only ['large']."""
        cand = make_candidate(size="small")
        vr = self._run([cand], make_filters(size=["large"]))
        assert len(vr.included_candidates) == 0

    def test_geography_filter_case_insensitive(self):
        """Geography filter should match case-insensitively ('us' matches 'US')."""
        cand = make_candidate(geography="US")
        vr = self._run([cand] * 3, make_filters(geographies=["us"]))
        assert len(vr.included_candidates) == 3

    def test_geography_filter_rejects_mismatch(self):
        """A 'DE' candidate should fail a filter requiring only ['US', 'CA']."""
        cand = make_candidate(geography="DE")
        vr = self._run([cand], make_filters(geographies=["US", "CA"]))
        assert len(vr.included_candidates) == 0

    def test_revenue_band_inclusive_boundaries(self):
        """Revenue band filter should be inclusive: min ≤ revenue ≤ max.

        A candidate with revenue_ltm = 100 should pass band [100, 500].
        """
        cand = make_candidate(revenue_ltm=100.0)
        vr = self._run([cand] * 3, make_filters(revenue_band=RevenueBand(min=100.0, max=500.0)))
        assert len(vr.included_candidates) == 3

    def test_revenue_band_rejects_below_min(self):
        """A candidate with revenue below the band minimum should be excluded."""
        cand = make_candidate(revenue_ltm=50.0)
        vr = self._run([cand], make_filters(revenue_band=RevenueBand(min=100.0, max=500.0)))
        assert len(vr.included_candidates) == 0

    def test_revenue_band_rejects_above_max(self):
        """A candidate with revenue above the band maximum should be excluded."""
        cand = make_candidate(revenue_ltm=600.0)
        vr = self._run([cand], make_filters(revenue_band=RevenueBand(min=100.0, max=500.0)))
        assert len(vr.included_candidates) == 0

    def test_industry_keyword_substring_match(self):
        """Industry keyword filter uses substring matching in tags.

        Tag 'cloud-saas' should match keyword 'saas'.
        """
        cand = make_candidate(industry_tags=["cloud-saas", "analytics"])
        vr = self._run([cand] * 3, make_filters(industry_keywords=["saas"]))
        assert len(vr.included_candidates) == 3

    def test_industry_keyword_no_match(self):
        """If no tag contains any keyword, the candidate should be excluded."""
        cand = make_candidate(industry_tags=["hardware", "networking"])
        vr = self._run([cand], make_filters(industry_keywords=["software"]))
        assert len(vr.included_candidates) == 0

    def test_ev_not_positive_excluded(self):
        """Candidates with ev ≤ 0 should be excluded with reason 'filter_ev_not_positive'."""
        cand = make_candidate(ev=0.0)
        vr = self._run([cand], make_filters())
        excluded = [d for d in vr.match_details if not d.included]
        assert len(excluded) == 1
        assert excluded[0].excluded_reason == "filter_ev_not_positive"

    def test_negative_ev_excluded(self):
        """Candidates with negative EV should also be excluded."""
        cand = make_candidate(ev=-500.0)
        vr = self._run([cand], make_filters())
        excluded = [d for d in vr.match_details if not d.included]
        assert excluded[0].excluded_reason == "filter_ev_not_positive"

    def test_empty_filters_pass_everything(self):
        """With all filter lists empty and no revenue_band, every candidate passes
        (as long as ev > 0).
        """
        cand = make_candidate()
        vr = self._run([cand] * 5, make_filters())
        assert len(vr.included_candidates) == 5

    def test_exclusion_reason_priority_sector_over_size(self):
        """When both sector and size fail, excluded_reason should be 'filter_sector'
        (higher priority than filter_size).
        """
        cand = make_candidate(sector="Wrong", size="wrong")
        vr = self._run(
            [cand],
            make_filters(sector=["Application Software"], size=["mid"]),
        )
        excluded = [d for d in vr.match_details if not d.included]
        assert excluded[0].excluded_reason == "filter_sector"


# ═══════════════════════════════════════════════════════════════════════════
# Sort and cap
# ═══════════════════════════════════════════════════════════════════════════


class TestSortAndCap:
    """Tests for deterministic sort ordering and max_comps cap."""

    def test_sort_by_ticker(self):
        """Candidates should be sorted alphabetically by ticker.

        Input order: CCC, AAA, BBB → sorted order: AAA, BBB, CCC.
        """
        candidates = [
            make_candidate(company_id="C-3", ticker="CCC", ev=800.0, revenue_ltm=100.0),
            make_candidate(company_id="C-1", ticker="AAA", ev=1000.0, revenue_ltm=100.0),
            make_candidate(company_id="C-2", ticker="BBB", ev=1200.0, revenue_ltm=100.0),
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(max_comps=10),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        tickers = [c.ticker for c in vr.included_candidates]
        assert tickers == ["AAA", "BBB", "CCC"]

    def test_max_comps_cap_excludes_excess(self):
        """When more candidates pass filters than max_comps, excess should be
        excluded with reason 'limit_max_comps' and still have a selection_rank.
        """
        candidates = [
            make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=1000.0, revenue_ltm=100.0)
            for i in range(5)
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(max_comps=3),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert len(vr.included_candidates) == 3
        assert vr.matched_before_limit == 5
        excluded = [d for d in vr.match_details if d.excluded_reason == "limit_max_comps"]
        assert len(excluded) == 2
        # Excess should still have a rank (4, 5)
        assert all(d.selection_rank is not None for d in excluded)

    def test_matched_before_limit_reflects_filter_survivors(self):
        """matched_before_limit should count how many passed filters before capping."""
        candidates = [
            make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}") for i in range(7)
        ]
        vr = run_valuation(
            candidates=candidates,
            selection=make_selection(max_comps=4),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        assert vr.matched_before_limit == 7
        assert len(vr.included_candidates) == 4


# ═══════════════════════════════════════════════════════════════════════════
# CLI integration test (against examples/request.json)
# ═══════════════════════════════════════════════════════════════════════════


class TestCLIHappyPath:
    """Integration tests that run the CLI end-to-end and validate output."""

    def _run_cli(self, *extra_args: str) -> subprocess.CompletedProcess:
        """Helper to invoke the CLI as a subprocess."""
        cmd = [
            sys.executable, "-m", "valuation_audit_trail.cli",
            "--input", str(EXAMPLES_DIR / "request.json"),
            *extra_args,
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        )

    def test_cli_exits_zero(self):
        """CLI should exit with code 0 for a valid request."""
        result = self._run_cli()
        assert result.returncode == 0, result.stderr

    def test_cli_output_is_valid_json(self):
        """CLI stdout should be valid JSON."""
        result = self._run_cli()
        report = json.loads(result.stdout)
        assert isinstance(report, dict)

    def test_cli_report_status_ok(self):
        """The report status should be 'ok' for the canonical example request."""
        result = self._run_cli()
        report = json.loads(result.stdout)
        assert report["status"] == "ok"

    def test_cli_report_has_expected_fair_value(self):
        """The canonical request should produce EV point=5000.0.

        3 comps included (HUBS=10.0, SHOP=11.25, WDAY=10.0).
        Sorted adjusted: [10.0, 10.0, 11.25].
        q50 nearest_rank: ceil(0.5*3)=2 → index 1 → 10.0.
        EV_point = 500 * 10.0 = 5000.0.
        """
        result = self._run_cli()
        report = json.loads(result.stdout)
        assert report["valuation"]["fair_value"]["point"] == 5000.0

    def test_cli_report_echoes_request_id(self):
        """Report should echo back the request_id from the input."""
        result = self._run_cli()
        report = json.loads(result.stdout)
        assert report["request_id"] == "req_2026_02_16_001"

    def test_cli_report_validates_against_schema(self):
        """The generated report should pass validation against report.schema.json."""
        import jsonschema

        result = self._run_cli()
        report = json.loads(result.stdout)
        schema = json.loads((SCHEMAS_DIR / "report.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(report))
        assert errors == [], [e.message for e in errors]

    def test_cli_output_to_file(self, tmp_path):
        """When --output is specified, the report should be written to that file
        instead of stdout.
        """
        out_file = tmp_path / "report.json"
        result = self._run_cli("--output", str(out_file))
        assert result.returncode == 0
        assert out_file.exists()
        report = json.loads(out_file.read_text())
        assert report["status"] == "ok"
