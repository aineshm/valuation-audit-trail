"""Explain boundary for field-level derivation expansion and step tracing.

Given a completed report and a field_path (e.g. "valuation.fair_value.point"),
walks the provenance DAG backwards from the target node to all roots, then
returns a JSON-serializable dict matching the --explain output contract in
docs/design.md.
"""

from __future__ import annotations

from typing import Any

from valuation_audit_trail.models import (
    AssumptionEntry,
    ProvenanceNode,
    SourceEntry,
)


def explain_field(
    field_path: str,
    provenance_nodes: list[ProvenanceNode],
    assumptions_used: list[AssumptionEntry],
    sources: list[SourceEntry],
) -> dict:
    """Build the --explain output for a given field_path.

    Steps:
        1. Find the target node by field_path match.
        2. Walk parent_ids recursively to collect the full derivation chain.
        3. Topological-sort the chain (roots first → target last).
        4. Collect referenced assumption_ids and source_ids across the chain.
        5. Return the explain dict.

    Returns:
        {
            "field_path": str,
            "value": <target node output>,
            "derivation_chain": [ { node_id, formula, inputs, assumption_ids,
                                     source_ids, output }, ... ],
            "assumptions": [ { id, name, value }, ... ],
            "sources": [ { id, provider, dataset, ... }, ... ],
        }

    Raises:
        ValueError: if field_path does not match any provenance node.
    """
    # Build index for O(1) lookup by id
    node_index = {n.id: n for n in provenance_nodes}

    # 1. Find target node
    target = _find_node_by_field_path(field_path, provenance_nodes)

    # 2 & 3. Walk ancestors, topologically sorted (roots first)
    chain = _walk_ancestors(target, node_index)

    # 4. Collect referenced assumptions and sources
    assumptions = _collect_referenced_assumptions(chain, assumptions_used)
    sources_out = _collect_referenced_sources(chain, sources)

    # 5. Build output
    derivation_chain = [
        {
            "node_id": n.id,
            "formula": n.formula,
            "inputs": n.inputs,
            "assumption_ids": n.assumption_ids,
            "source_ids": n.source_ids,
            "output": n.output,
        }
        for n in chain
    ]

    return {
        "field_path": field_path,
        "value": target.output,
        "derivation_chain": derivation_chain,
        "assumptions": assumptions,
        "sources": sources_out,
    }


def _find_node_by_field_path(
    field_path: str,
    nodes: list[ProvenanceNode],
) -> ProvenanceNode:
    """Locate the provenance node whose field_path matches the query.

    Raises ValueError if not found.
    """
    for node in nodes:
        if node.field_path == field_path:
            return node
    raise ValueError(
        f"No provenance node found for field_path {field_path!r}. "
        f"Available: {[n.field_path for n in nodes]}"
    )


def _walk_ancestors(
    target: ProvenanceNode,
    node_index: dict[str, ProvenanceNode],
) -> list[ProvenanceNode]:
    """BFS/DFS walk up parent_ids to collect all ancestor nodes + the target.

    Returns:
        Topologically ordered list (roots first, target last).
    """
    visited: set[str] = set()
    order: list[ProvenanceNode] = []

    def _dfs(node: ProvenanceNode) -> None:
        if node.id in visited:
            return
        visited.add(node.id)
        for pid in node.parent_ids:
            parent = node_index.get(pid)
            if parent is not None:
                _dfs(parent)
        order.append(node)

    _dfs(target)
    return order


def _collect_referenced_assumptions(
    chain: list[ProvenanceNode],
    all_assumptions: list[AssumptionEntry],
) -> list[dict]:
    """Return the subset of assumptions_used referenced by any node in the chain."""
    referenced_ids: set[str] = set()
    for node in chain:
        referenced_ids.update(node.assumption_ids)
    return [a.to_dict() for a in all_assumptions if a.id in referenced_ids]


def _collect_referenced_sources(
    chain: list[ProvenanceNode],
    all_sources: list[SourceEntry],
) -> list[dict]:
    """Return the subset of sources referenced by any node in the chain."""
    referenced_ids: set[str] = set()
    for node in chain:
        referenced_ids.update(node.source_ids)
    return [s.to_dict() for s in all_sources if s.id in referenced_ids]
