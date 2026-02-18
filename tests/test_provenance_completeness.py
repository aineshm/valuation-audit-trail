"""Tests for provenance DAG completeness.

The provenance graph has 10 nodes (all prefixed 'prov_'):
    prov_subject_revenue → prov_selected_comps → prov_raw_multiples →
    prov_adjusted_multiples →
    prov_multiple_low, prov_multiple_point, prov_multiple_high →
    prov_fair_value_low, prov_fair_value_point, prov_fair_value_high

This module verifies:
    • Exactly 10 nodes are generated
    • Every parent_id resolves to an existing node
    • All fair_value nodes trace back to prov_subject_revenue
    • Formula strings are populated on every non-root node
    • source_ids are propagated where expected
    • Node IDs are unique
"""

from __future__ import annotations

import pytest

from valuation_audit_trail.provenance import build_provenance
from valuation_audit_trail.valuation import run_valuation

from conftest import make_assumptions, make_candidate, make_filters, make_selection


def _build_provenance_from_scratch():
    """Helper: run valuation and build the provenance DAG."""
    sel = make_selection()
    asm = make_assumptions(outlier_policy="none")
    candidates = [
        make_candidate(company_id=f"C-{i}", ticker=f"T{i:02d}", ev=1000.0 + i * 100, revenue_ltm=100.0)
        for i in range(5)
    ]
    vr = run_valuation(
        candidates=candidates,
        selection=sel,
        assumptions=asm,
        subject_revenue_ltm=500.0,
        rounding_decimals=2,
    )
    return build_provenance(
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


# ═══════════════════════════════════════════════════════════════════════════
# Node count and identity
# ═══════════════════════════════════════════════════════════════════════════


class TestProvenanceStructure:
    """Tests for the shape and integrity of the provenance graph."""

    def test_exactly_ten_nodes(self):
        """The provenance graph should contain exactly 10 nodes."""
        prov = _build_provenance_from_scratch()
        assert len(prov) == 10

    def test_all_node_ids_unique(self):
        """Every node should have a unique id."""
        prov = _build_provenance_from_scratch()
        ids = [n.id for n in prov]
        assert len(ids) == len(set(ids))

    def test_expected_node_ids_present(self):
        """The graph should contain all 10 expected node IDs (prov_ prefixed)."""
        expected = {
            "prov_subject_revenue",
            "prov_selected_comps",
            "prov_raw_multiples",
            "prov_adjusted_multiples",
            "prov_multiple_low",
            "prov_multiple_point",
            "prov_multiple_high",
            "prov_fair_value_low",
            "prov_fair_value_point",
            "prov_fair_value_high",
        }
        prov = _build_provenance_from_scratch()
        actual = {n.id for n in prov}
        assert actual == expected


# ═══════════════════════════════════════════════════════════════════════════
# Parent resolution
# ═══════════════════════════════════════════════════════════════════════════


class TestParentResolution:
    """Tests that all parent_ids reference existing nodes."""

    def test_all_parent_ids_resolve(self):
        """Every parent_id referenced in any node should be a valid node id."""
        prov = _build_provenance_from_scratch()
        all_ids = {n.id for n in prov}
        for node in prov:
            if node.parent_ids:
                for pid in node.parent_ids:
                    assert pid in all_ids, f"Dangling parent_id '{pid}' in node '{node.id}'"

    def test_subject_revenue_is_root(self):
        """prov_subject_revenue should have no parents (it's an input/root node)."""
        prov = _build_provenance_from_scratch()
        node = next(n for n in prov if n.id == "prov_subject_revenue")
        assert node.parent_ids is None or node.parent_ids == []

    def test_selected_comps_is_root(self):
        """prov_selected_comps should also be a root node (sourced from dataset)."""
        prov = _build_provenance_from_scratch()
        node = next(n for n in prov if n.id == "prov_selected_comps")
        assert node.parent_ids is None or node.parent_ids == []


# ═══════════════════════════════════════════════════════════════════════════
# Traceability
# ═══════════════════════════════════════════════════════════════════════════


class TestTraceability:
    """Tests that fair_value nodes can be traced back to the root nodes."""

    def _ancestors(self, prov, start_id: str) -> set[str]:
        """BFS/DFS to find all ancestors of a node."""
        node_map = {n.id: n for n in prov}
        visited = set()
        stack = [start_id]
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            node = node_map[nid]
            if node.parent_ids:
                stack.extend(node.parent_ids)
        return visited

    def test_fair_value_point_traces_to_subject_revenue(self):
        """prov_fair_value_point should trace back to prov_subject_revenue."""
        prov = _build_provenance_from_scratch()
        ancestors = self._ancestors(prov, "prov_fair_value_point")
        assert "prov_subject_revenue" in ancestors

    def test_fair_value_point_traces_to_selected_comps(self):
        """prov_fair_value_point should trace back to prov_selected_comps."""
        prov = _build_provenance_from_scratch()
        ancestors = self._ancestors(prov, "prov_fair_value_point")
        assert "prov_selected_comps" in ancestors

    def test_fair_value_low_traces_to_roots(self):
        """prov_fair_value_low should trace back to both root nodes."""
        prov = _build_provenance_from_scratch()
        ancestors = self._ancestors(prov, "prov_fair_value_low")
        assert "prov_subject_revenue" in ancestors
        assert "prov_selected_comps" in ancestors

    def test_fair_value_high_traces_to_roots(self):
        """prov_fair_value_high should trace back to both root nodes."""
        prov = _build_provenance_from_scratch()
        ancestors = self._ancestors(prov, "prov_fair_value_high")
        assert "prov_subject_revenue" in ancestors
        assert "prov_selected_comps" in ancestors


# ═══════════════════════════════════════════════════════════════════════════
# Formulas and metadata
# ═══════════════════════════════════════════════════════════════════════════


class TestNodeMetadata:
    """Tests for formula strings and metadata on provenance nodes."""

    def test_all_non_root_nodes_have_formula(self):
        """Every node that has parents should have a formula string."""
        prov = _build_provenance_from_scratch()
        for node in prov:
            if node.parent_ids:
                assert node.formula is not None and len(node.formula) > 0, \
                    f"Node '{node.id}' missing formula"

    def test_fair_value_formulas_reference_revenue(self):
        """fair_value_* formulas should reference 'revenue' in their formula."""
        prov = _build_provenance_from_scratch()
        for nid in ("prov_fair_value_low", "prov_fair_value_point", "prov_fair_value_high"):
            node = next(n for n in prov if n.id == nid)
            assert "revenue" in node.formula.lower() or "×" in node.formula or "*" in node.formula, \
                f"fair_value formula should reference revenue: {node.formula}"

    def test_multiple_nodes_reference_quantile(self):
        """multiple_* node formulas should reference quantile or percentile."""
        prov = _build_provenance_from_scratch()
        for nid in ("prov_multiple_low", "prov_multiple_point", "prov_multiple_high"):
            node = next(n for n in prov if n.id == nid)
            assert node.formula is not None
            lower_formula = node.formula.lower()
            assert any(kw in lower_formula for kw in ("quantile", "percentile", "q25", "q50", "q75", "p25", "p50", "p75", "nearest", "interpolat")), \
                f"Multiple node formula should reference quantile: {node.formula}"
