# valuation-audit-trail

Auditable valuation backend workflow for comps-based EV/Revenue calculations.

## Purpose

This project is designed for **traceability, reproducibility, and clear audit documentation**.
It does **not** claim valuation outputs are "correct"; it focuses on showing exactly how outputs
are derived — every number traces back to explicit inputs, assumptions, and data sources through
a provenance DAG.

## Status

The engine is **fully implemented** and runs end-to-end with the mock provider (`mock_v1`).
All 8 source modules are complete with zero TODOs remaining.

### What works today

| Capability | Status |
|---|---|
| Schema-validated request ingestion | ✅ |
| Multi-dimensional comp filtering (sector, size, geography, industry keywords, revenue band) | ✅ |
| Deterministic selection via stable `sort_key` | ✅ |
| Outlier policies: none, trim, winsorize | ✅ |
| Quantile methods: `nearest_rank`, `linear_interpolation` | ✅ |
| Full provenance DAG (10 nodes, every fair-value number traceable) | ✅ |
| `--explain <field_path>` derivation-chain output | ✅ |
| Run manifest with SHA-256 input/output/provider hashes | ✅ |
| Error path (schema errors, business-rule errors, comp-count errors) | ✅ |
| Determinism (identical input + provider → bit-identical output) | ✅ |

## Quick Start

```bash
# Install (editable, with dev extras)
pip install -e ".[dev]"

# Run a valuation
PYTHONPATH=src python3 -m valuation_audit_trail.cli --input examples/request.json

# Write output to a file
PYTHONPATH=src python3 -m valuation_audit_trail.cli \
  --input examples/request.json \
  --output out/report.json

# Explain how a specific number was derived
PYTHONPATH=src python3 -m valuation_audit_trail.cli \
  --input examples/request.json \
  --explain "valuation.fair_value.point"

# Run the winsorize demo (broader filters, outlier clamping at q=0.2)
PYTHONPATH=src python3 -m valuation_audit_trail.cli \
  --input examples/request_winsorize.json
```

Or use the installed entry point (after `pip install -e .`):

```bash
valuation-audit --input examples/request.json
```

## Project Structure

```
valuation-audit-trail/
├── data/
│   └── mock_comps_v1.json            # 15-company mock dataset
├── docs/
│   ├── design.md                     # Architecture & determinism rules
│   └── error_codes.md                # Error/warning code reference
├── examples/
│   ├── request.json                  # Canonical request (sector/size/geo filtering)
│   ├── report.json                   # Expected success report (3 comps)
│   ├── request_winsorize.json        # Winsorize demo (10 comps, q=0.2)
│   ├── report_winsorize.json         # Winsorize report (outlier 14→12 clamped)
│   ├── report_error.json             # Error-path report (E_REVENUE_NOT_POSITIVE)
│   └── explain_output.json           # --explain derivation chain output
├── schemas/
│   ├── input.schema.json             # JSON Schema 2020-12 for requests
│   └── report.schema.json            # JSON Schema 2020-12 for reports
├── src/valuation_audit_trail/
│   ├── cli.py                        # CLI orchestration (12-step pipeline)
│   ├── models.py                     # Typed dataclasses aligned to schemas
│   ├── valuation.py                  # All math: filtering, outliers, quantiles, EV
│   ├── provenance.py                 # 10-node DAG builder
│   ├── manifest.py                   # Run manifest + canonical SHA-256 hashing
│   ├── errors.py                     # Coded errors/warnings with JSON-path refs
│   ├── explain.py                    # --explain derivation-chain walker
│   └── providers.py                  # Provider boundary (dataset loading + fingerprinting)
├── tests/                            # Test stubs (7 files, to be implemented)
└── pyproject.toml                    # Build config (Python ≥3.11, jsonschema ≥4.20)
```

## Schemas

### `input.schema.json`

- **Filters**: `sector` (array), `size` (enum: small/mid/large), `industry_keywords`, `geographies`, `revenue_band` (min/max)
- **Determinism**: Requires `sort_key` (e.g., `["ticker", "company_id"]`) and `quantile_method` (`nearest_rank` | `linear_interpolation`)
- **Assumptions**: `outlier_policy` (none/trim/winsorize), `outlier_quantile`, `quantile_method`
- **Schema-level enforcement**: `revenue_ltm > 0` via `exclusiveMinimum`

### `report.schema.json`

- **Conditionality**: `oneOf` enforces `status="ok" → valuation object` / `status="error" → valuation null`
- **Match details**: Every candidate gets `matched_filters` (7 booleans), `excluded_reason`, `selection_rank`, and `ticker`
- **Config snapshot**: Full echo of all result-driving inputs (comps_selection, provider_overrides, assumptions, rounding_decimals)
- **Provenance**: Node array with DAG linkage via `parent_ids`

## Mock Dataset

`data/mock_comps_v1.json` contains **15 companies** loosely inspired by public software companies:

| Sector | Size | Companies |
|---|---|---|
| Application Software | large | CRM, NOW, SAP |
| Application Software | mid | WDAY, HUBS, SHOP, TEAM |
| Application Software | small | BILL, MNDY |
| Infrastructure Software | mid | DDOG, ZS, MDB, NET |
| Infrastructure Software | small | ESTC, CFLT |

Geographies: US, CA, DE, AU, NL, IL. EV/Revenue multiples range ~8×–15×.

## Documentation

- **[docs/design.md](docs/design.md)** — Architecture, module boundaries, provenance model, determinism rules, pipeline walkthrough, `--explain` contract
- **[docs/error_codes.md](docs/error_codes.md)** — Complete error/warning code reference with thresholds and examples

## Non-Goals

- No claim of valuation correctness — only deterministic and auditable transformation
- No live market data connectors (mock provider only)
- No UI or API server (CLI-only)
