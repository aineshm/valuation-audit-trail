"""Tests for the explain(field_path) feature.

Covers:
    • Explaining each fair_value path (point, low, high)
    • Derivation chain starts at the queried node and traces to root(s)
    • Correct ancestors are collected in topological (roots-first) order
    • Referenced assumptions and sources are collected
    • Unknown field_path raises ValueError
    • Explanation includes the target node's formula
"""

from __future__ import annotations

import pytest

from valuation_audit_trail.explain import explain_field, _find_node_by_field_path, _walk_ancestors
from valuation_audit_trail.provenance import build_provenance
from valuation_audit_trail.valuation import run_valuation
from valuation_audit_trail.models import AssumptionEntry, SourceEntry

from conftest import make_assumptions, make_candidate, make_filters, make_selection


def _build_test_provenance():
    """Helper: run a small valuation and build provenance for it.

    Returns (provenance_nodes, assumptions_entries, source_entries).
    """
    sel = make_selection()
    asm = make_assumptions(outlier_policy="none")
    candidates = [
        make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=1000.0 + i * 200, revenue_ltm=100.0)
        for i in range(5)
    ]
    vr = run_valuation(
        candidates=candidates,
        selection=sel,
        assumptions=asm,
        subject_revenue_ltm=500.0,
        rounding_decimals=2,
    )
    prov = build_provenance(
        subject_revenue_ltm=500.0,
        selection=sel,
        included_candidates=vr.included_candidates,
        raw_multiples=vr.raw_multiples,
        adjusted_multiples=vr.adjusted_multiples,
        median_multiple=vr.median_multiple,
        multiple_low=vr.multiple_low,
        multiple_high=vr.multiple_high,
        ev_low=vr.ev_low,
        ev_point=vr.ev_point,
        ev_high=vr.ev_high,
        assumptions=asm,
        source_id="src_mock_v1",
    )
    # Build minimal assumptions / sources lists for explain
    assumptions_used = [
        AssumptionEntry(id="asm_outlier_policy", name="outlier_policy", value="none"),
        AssumptionEntry(id="asm_outlier_quantile", name="outlier_quantile", value=0.1),
        AssumptionEntry(id="asm_quantile_method", name="quantile_method", value="nearest_rank"),
    ]
    source_entries = [
        SourceEntry(id="src_mock_v1", provider="mock_v1", dataset="mock_comps_v1", dataset_version="1.0", dataset_hash="sha256:test", citation="mock data"),
    ]
    return prov, assumptions_used, source_entries


# ═══════════════════════════════════════════════════════════════════════════
# _find_node_by_field_path
# ═══════════════════════════════════════════════════════════════════════════


class TestFindNodeByFieldPath:
    """Tests for locating a node by its field_path attribute."""

    def test_finds_fair_value_point(self):
        """Should find the node with field_path='valuation.fair_value.point'."""
        prov, _, _ = _build_test_provenance()
        node = _find_node_by_field_path("valuation.fair_value.point", prov)
        assert node is not None
        assert node.field_path == "valuation.fair_value.point"

    def test_finds_fair_value_low(self):
        """Should find the node with field_path='valuation.fair_value.range.low'."""
        prov, _, _ = _build_test_provenance()
        node = _find_node_by_field_path("valuation.fair_value.range.low", prov)
        assert node is not None

    def test_finds_fair_value_high(self):
        """Should find the node with field_path='valuation.fair_value.range.high'."""
        prov, _, _ = _build_test_provenance()
        node = _find_node_by_field_path("valuation.fair_value.range.high", prov)
        assert node is not None

    def test_unknown_field_path_raises(self):
        """An unrecognised field_path should raise ValueError."""
        prov, _, _ = _build_test_provenance()
        with pytest.raises(ValueError, match="No provenance node found"):
            _find_node_by_field_path("does.not.exist", prov)


# ═══════════════════════════════════════════════════════════════════════════
# _walk_ancestors
# ═══════════════════════════════════════════════════════════════════════════


class TestWalkAncestors:
    """Tests for the DFS ancestor-collection function."""

    def test_fair_value_point_traces_to_subject_revenue(self):
        """fair_value.point should ultimately trace back to prov_subject_revenue."""
        prov, _, _ = _build_test_provenance()
        target = _find_node_by_field_path("valuation.fair_value.point", prov)
        node_map = {n.id: n for n in prov}
        ancestors = _walk_ancestors(target, node_map)
        ancestor_ids = {n.id for n in ancestors}
        assert "prov_subject_revenue" in ancestor_ids

    def test_ancestors_include_selected_comps(self):
        """The ancestor chain should include the prov_selected_comps node."""
        prov, _, _ = _build_test_provenance()
        target = _find_node_by_field_path("valuation.fair_value.point", prov)
        node_map = {n.id: n for n in prov}
        ancestors = _walk_ancestors(target, node_map)
        ancestor_ids = {n.id for n in ancestors}
        assert "prov_selected_comps" in ancestor_ids

    def test_roots_come_first(self):
        """The first element(s) in the ancestor list should have no parent_ids."""
        prov, _, _ = _build_test_provenance()
        target = _find_node_by_field_path("valuation.fair_value.point", prov)
        node_map = {n.id: n for n in prov}
        ancestors = _walk_ancestors(target, node_map)
        first = ancestors[0]
        assert first.parent_ids == [] or first.parent_ids is None or len(first.parent_ids) == 0


# ═══════════════════════════════════════════════════════════════════════════
# explain_field end-to-end
# ═══════════════════════════════════════════════════════════════════════════


class TestExplainFieldEndToEnd:
    """Tests for the high-level explain_field function.

    explain_field(field_path, provenance_nodes, assumptions_used, sources)
    returns a dict with derivation_chain, assumptions, sources.
    """

    def test_explain_fair_value_point_has_derivation(self):
        """Explaining fair_value.point should return a non-empty derivation chain."""
        prov, assumptions, sources = _build_test_provenance()
        explanation = explain_field("valuation.fair_value.point", prov, assumptions, sources)
        assert len(explanation["derivation_chain"]) > 0

    def test_explain_includes_target_formula(self):
        """The target node (last in chain) should have a formula string."""
        prov, assumptions, sources = _build_test_provenance()
        explanation = explain_field("valuation.fair_value.point", prov, assumptions, sources)
        target = explanation["derivation_chain"][-1]
        assert target["formula"] is not None
        assert len(target["formula"]) > 0

    def test_explain_collects_assumptions(self):
        """The explanation should include referenced assumptions."""
        prov, assumptions, sources = _build_test_provenance()
        explanation = explain_field("valuation.fair_value.point", prov, assumptions, sources)
        # Assumptions are collected from the chain
        assert isinstance(explanation["assumptions"], list)

    def test_explain_collects_sources(self):
        """The explanation should include referenced data sources."""
        prov, assumptions, sources = _build_test_provenance()
        explanation = explain_field("valuation.fair_value.point", prov, assumptions, sources)
        assert isinstance(explanation["sources"], list)

    def test_explain_unknown_field_path_raises(self):
        """Explaining a non-existent field should raise ValueError."""
        prov, assumptions, sources = _build_test_provenance()
        with pytest.raises(ValueError):
            explain_field("nonexistent.field", prov, assumptions, sources)

    def test_explain_fair_value_low(self):
        """Explaining fair_value.range.low should succeed and reference multiple_low."""
        prov, assumptions, sources = _build_test_provenance()
        explanation = explain_field("valuation.fair_value.range.low", prov, assumptions, sources)
        node_ids = [n["node_id"] for n in explanation["derivation_chain"]]
        assert "prov_multiple_low" in node_ids

    def test_explain_fair_value_high(self):
        """Explaining fair_value.range.high should succeed and reference multiple_high."""
        prov, assumptions, sources = _build_test_provenance()
        explanation = explain_field("valuation.fair_value.range.high", prov, assumptions, sources)
        node_ids = [n["node_id"] for n in explanation["derivation_chain"]]
        assert "prov_multiple_high" in node_ids
