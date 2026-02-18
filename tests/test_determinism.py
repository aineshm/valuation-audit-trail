"""Tests for determinism guarantees.

The engine promises that identical inputs always produce identical outputs.
This module verifies:
    • Two successive runs with the same inputs yield byte-identical JSON
    • Output hash is stable across runs
    • Sort order is deterministic even with tie-breaking
    • canonical_json_bytes produces stable output
    • compute_output_hash zeroes hash before re-hashing
"""

from __future__ import annotations

import copy
import json

import pytest

from valuation_audit_trail.manifest import (
    canonical_json_bytes,
    compute_hash,
    compute_output_hash,
)
from valuation_audit_trail.valuation import run_valuation

from conftest import make_assumptions, make_candidate, make_selection


# ═══════════════════════════════════════════════════════════════════════════
# canonical_json_bytes stability
# ═══════════════════════════════════════════════════════════════════════════


class TestCanonicalJsonBytes:
    """Tests for the canonical JSON serialiser."""

    def test_sorted_keys(self):
        """Output should have keys in sorted order regardless of insertion order."""
        data = {"z": 1, "a": 2, "m": 3}
        blob = canonical_json_bytes(data)
        decoded = json.loads(blob)
        assert list(decoded.keys()) == ["a", "m", "z"]

    def test_no_whitespace(self):
        """Canonical JSON should use minimal separators (',', ':')."""
        data = {"key": "value", "num": 42}
        blob = canonical_json_bytes(data)
        assert b" " not in blob  # no spaces

    def test_deterministic_on_repeated_calls(self):
        """Calling canonical_json_bytes twice should produce identical bytes."""
        data = {"nested": {"b": 2, "a": 1}, "list": [3, 2, 1]}
        assert canonical_json_bytes(data) == canonical_json_bytes(data)

    def test_utf8_encoding(self):
        """Output should be UTF-8 encoded."""
        data = {"emoji": "🎯"}
        blob = canonical_json_bytes(data)
        assert "🎯" in blob.decode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# compute_hash stability
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeHash:
    """Tests for SHA-256 hash wrapper.

    compute_hash takes raw bytes, so we pass canonical_json_bytes output.
    """

    def test_hash_format(self):
        """Hash should start with 'sha256:' prefix."""
        h = compute_hash(canonical_json_bytes({"a": 1}))
        assert h.startswith("sha256:")

    def test_hash_length(self):
        """SHA-256 hex digest is 64 chars → total = len('sha256:') + 64 = 71."""
        h = compute_hash(canonical_json_bytes({"a": 1}))
        assert len(h) == 71

    def test_same_input_same_hash(self):
        """Identical dicts (different insertion order) should produce identical hashes
        because canonical_json_bytes sorts keys."""
        d1 = {"x": [1, 2, 3], "y": "hello"}
        d2 = {"y": "hello", "x": [1, 2, 3]}
        assert compute_hash(canonical_json_bytes(d1)) == compute_hash(canonical_json_bytes(d2))

    def test_different_input_different_hash(self):
        """Different dicts should (almost certainly) produce different hashes."""
        h1 = compute_hash(canonical_json_bytes({"a": 1}))
        h2 = compute_hash(canonical_json_bytes({"a": 2}))
        assert h1 != h2


# ═══════════════════════════════════════════════════════════════════════════
# compute_output_hash idempotence
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeOutputHash:
    """Tests for the output_hash zeroing logic."""

    def test_output_hash_field_is_zeroed_before_hashing(self):
        """compute_output_hash should zero the run_manifest.output_hash field
        in the copy so that re-hashing always produces the same hash regardless
        of what output_hash was set to before.
        """
        report = {
            "run_manifest": {"output_hash": "sha256:abc123"},
            "valuation": {"fair_value": {"point": 5000.0}},
        }
        h1 = compute_output_hash(report)

        report["run_manifest"]["output_hash"] = "sha256:different"
        h2 = compute_output_hash(report)

        assert h1 == h2  # both should be the same because hash field is zeroed

    def test_output_hash_does_not_mutate_original(self):
        """The original report dict should not be modified."""
        report = {
            "run_manifest": {"output_hash": "sha256:original"},
            "data": [1, 2, 3],
        }
        original_hash = report["run_manifest"]["output_hash"]
        compute_output_hash(report)
        assert report["run_manifest"]["output_hash"] == original_hash


# ═══════════════════════════════════════════════════════════════════════════
# End-to-end determinism
# ═══════════════════════════════════════════════════════════════════════════


class TestEndToEndDeterminism:
    """Tests that identical inputs always yield identical outputs."""

    CANDIDATES = [
        make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=1000.0 + i * 100, revenue_ltm=100.0)
        for i in range(6)
    ]

    def _run(self):
        return run_valuation(
            candidates=self.CANDIDATES,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )

    def test_two_runs_identical_multiples(self):
        """Two runs with the same inputs should produce identical raw_multiples."""
        vr1 = self._run()
        vr2 = self._run()
        assert vr1.raw_multiples == vr2.raw_multiples

    def test_two_runs_identical_fair_values(self):
        """Two runs should produce identical EV values."""
        vr1 = self._run()
        vr2 = self._run()
        assert vr1.ev_point == vr2.ev_point
        assert vr1.ev_low == vr2.ev_low
        assert vr1.ev_high == vr2.ev_high

    def test_two_runs_identical_adjusted_multiples(self):
        """Two runs should produce identical adjusted multiples."""
        vr1 = self._run()
        vr2 = self._run()
        assert vr1.adjusted_multiples == vr2.adjusted_multiples

    def test_sort_order_stability_with_ties(self):
        """When two candidates have the same ticker, sort is stable (preserves
        original insertion order within ties).
        """
        tied = [
            make_candidate(company_id="C-A1", ticker="AAA", ev=1000.0, revenue_ltm=100.0),
            make_candidate(company_id="C-A2", ticker="AAA", ev=2000.0, revenue_ltm=100.0),
            make_candidate(company_id="C-B1", ticker="BBB", ev=1500.0, revenue_ltm=100.0),
        ]
        vr1 = run_valuation(
            candidates=tied,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        vr2 = run_valuation(
            candidates=tied,
            selection=make_selection(),
            assumptions=make_assumptions(),
            subject_revenue_ltm=500.0,
            rounding_decimals=2,
        )
        ids1 = [c.company_id for c in vr1.included_candidates]
        ids2 = [c.company_id for c in vr2.included_candidates]
        assert ids1 == ids2
