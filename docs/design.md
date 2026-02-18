# Design Notes

## Goal

Build an auditable valuation workflow where every output number is traceable to explicit inputs, assumptions, and sources.  The system is **fully implemented** and runs end-to-end with the mock provider.

---

## Module Boundaries

| Module | Responsibility |
|---|---|
| `cli.py` | CLI arg parsing, 12-step orchestration pipeline, JSON I/O, schema validation |
| `models.py` | Typed dataclasses (`ValuationRequest`, `ValuationReport`, etc.) aligned to JSON schemas |
| `valuation.py` | All math: filtering, deterministic sort + cap, outlier treatment, quantile computation, EV calculation |
| `provenance.py` | 10-node DAG builder — one node per derivation step |
| `manifest.py` | Run manifest: timestamps, canonical SHA-256 hashing, config snapshot, provider fingerprints |
| `errors.py` | Coded error/warning constants, `Issue` dataclass, `validate_request()` + `validate_comps_count()` |
| `explain.py` | `--explain <field_path>` — DFS walks provenance DAG, returns topologically-sorted derivation chain |
| `providers.py` | Provider boundary: dataset loading from disk, `CompCandidate` parsing with universe stamping, SHA-256 fingerprinting |

---

## Pipeline Walkthrough (cli.py → main)

The orchestrator in `cli.py` executes these 12 steps:

```
 1. Parse CLI args (--input, --output, --explain)
 2. Load + JSON-schema-validate request against input.schema.json
 3. Convert raw dict → ValuationRequest model
 4. Pre-flight validation (errors.validate_request)
    → revenue_ltm > 0, revenue_band.min ≤ max
    → If errors → build error report, exit 1
 5. Load provider dataset (providers.load_dataset)
    → Returns DatasetPayload: candidates, source_entry, fingerprint, raw_meta
 6. Run valuation pipeline (valuation.run_valuation)
    → Filter → sort+cap → validate count → raw multiples → outliers → quantiles → EV
    → Returns ValuationResult with all intermediates
 7. Build provenance DAG (provenance.build_provenance)
    → 10 interconnected nodes from subject_revenue through fair_value
 8. Build comps_selection_result with filter echo
 9. Assemble full ValuationReport
10. Compute + patch output_hash (SHA-256 of canonical JSON)
11. If --explain: run explain.explain_field, emit explain JSON, exit 0
12. Emit report JSON to stdout or --output file
```

**Error path** (step 4 or step 6 errors): skips provenance, still builds manifest + sources for auditability.  Report has `status="error"`, `valuation=null`, non-empty `errors` array.

---

## Comp Selection & Filtering

### Filter Dimensions

Candidates are evaluated against **7 filter dimensions** (in priority order for exclusion reason):

| Priority | Dimension | Match Logic | Empty Filter Behavior |
|---|---|---|---|
| 1 | `universe` | Case-insensitive exact match: `candidate.universe == selection.universe` | No universe in request → pass |
| 2 | `sector` | Case-insensitive exact match against array | No filter → pass |
| 3 | `size` | Case-insensitive exact match (small/mid/large) | No filter → pass |
| 4 | `industry_keywords` | Case-insensitive substring in any `industry_tag` | No filter → pass |
| 5 | `geographies` | Case-insensitive exact match against array | No filter → pass |
| 6 | `revenue_band` | `min ≤ candidate.revenue_ltm ≤ max` | No filter → pass |
| 7 | `ev_positive` | `candidate.ev > 0` | Always applied |

**Source-agnostic universe filtering**: Each `CompCandidate` carries a `universe` field that is stamped by the provider at load time. The valuation layer compares `candidate.universe` against the requested `selection.universe` without any knowledge of the underlying data source format. This means a single dataset could contain candidates from multiple universes and the engine would correctly filter them.

When a candidate fails multiple filters, the `excluded_reason` is the **first** failing dimension in priority order (e.g., a candidate failing both sector and size gets `"filter_sector"`).

### Deterministic Selection

1. All candidates passing all filters are **stable-sorted** by `sort_key` attributes (e.g., `["ticker", "company_id"]`)
2. The first `max_comps` candidates are included
3. Excess candidates get `excluded_reason = "limit_max_comps"` and retain their `selection_rank`

### Match Details

Every candidate in the dataset appears in `match_details` with:
- `matched_filters`: 7 booleans showing which filters passed/failed
- `excluded_reason`: `null` (included) or one of `filter_universe`, `filter_sector`, `filter_size`, `filter_industry_keywords`, `filter_geographies`, `filter_revenue_band`, `filter_ev_not_positive`, `limit_max_comps`
- `selection_rank`: 1-based rank for included + limit-excluded; `null` for filter-excluded

---

## Valuation Math

### Raw Multiples

For each included comp: `multiple_i = ev_i / revenue_ltm_i`

Raw multiples are in comp selection order (not sorted).

### Outlier Treatment

Applied to **sorted** multiples.  Three policies:

| Policy | Behavior |
|---|---|
| `none` | Return sorted multiples as-is |
| `trim` | Remove values outside `[q_low, q_high]` |
| `winsorize` | Clamp values to `[q_low, q_high]` boundaries |

Boundaries are computed as:
- `q_low = quantile(sorted_multiples, outlier_quantile)`
- `q_high = quantile(sorted_multiples, 1 - outlier_quantile)`

### Quantile Methods

Two deterministic algorithms (declared in the request for reproducibility):

**`nearest_rank`** (default in examples):
```
rank = ceil(q × n)          # 1-based
result = sorted_values[rank - 1]   # 0-based index
```
Edge cases: `q ≤ 0 → index 0`, `q ≥ 1 → index n-1`. No averaging for even `n`.

**`linear_interpolation`** (matches numpy `method='linear'`):
```
virtual_index = q × (n - 1)
i = floor(virtual_index)
fraction = virtual_index - i
result = sorted_values[i] + fraction × (sorted_values[i+1] - sorted_values[i])
```

### Fair Value

```
EV_point = subject_revenue_ltm × q50(adjusted_multiples)
EV_low   = subject_revenue_ltm × q25(adjusted_multiples)
EV_high  = subject_revenue_ltm × q75(adjusted_multiples)
```

All values rounded to `config.rounding_decimals`.

---

## Provenance Model

Provenance is a **10-node directed acyclic graph** (DAG).  Each node captures:

| Field | Purpose |
|---|---|
| `id` | Stable identifier (e.g., `prov_fair_value_point`) |
| `field_path` | Report field this node computes (e.g., `valuation.fair_value.point`) |
| `formula` | Human-readable formula with actual values plugged in |
| `inputs` | Dict of input values used in this step |
| `assumption_ids` | Which assumptions influenced this step |
| `source_ids` | Which data sources contributed |
| `output` | The computed value |
| `parent_ids` | IDs of upstream nodes (enables DAG traversal) |

### Node Graph

```
prov_subject_revenue ─────────────────────────────────────┐
                                                          │
prov_selected_comps ──→ prov_raw_multiples ──→ prov_adjusted_multiples
                                                   │
                                    ┌──────────────┼──────────────┐
                                    ▼              ▼              ▼
                            prov_multiple_low  prov_multiple_point  prov_multiple_high
                                    │              │              │
                                    ▼              ▼              ▼
                            prov_fair_value_low  prov_fair_value_point  prov_fair_value_high
                                    ▲              ▲              ▲
                                    └──────────────┴──────────────┘
                                                   │
                                    prov_subject_revenue (also parent)
```

All `fair_value.*` numbers resolve to provenance node IDs via `valuation.fair_value.provenance_node_ids`.

---

## Determinism Rules

For identical request content and identical provider fingerprints:

1. **Numeric outputs** must be identical (bit-for-bit after rounding)
2. **Comps selection** must be identical (enforced via stable `sort_key` on filtered candidates)
3. **Derivation graph** must be structurally identical
4. **Run manifest hashes** must be stable under canonical serialization

### Canonical JSON Serialization

Used for all hashing (input, output, provider fingerprints):

```python
json.dumps(data, sort_keys=True, separators=(',', ':'))
# SHA-256 of UTF-8 bytes → "sha256:<hex>"
```

### Key Reproducibility Controls

| Control | Mechanism |
|---|---|
| Stable sort key | Request declares `sort_key` → deterministic comp ordering regardless of dataset file order |
| Quantile method | Request declares `quantile_method` → reproducible quantile calculations across environments |
| Canonical hashing | `sort_keys=True, separators=(',',':')` → same hash on any platform |
| Provider fingerprint | SHA-256 of raw data file bytes → detects any data change |
| Hard constants | `MIN_COMPS=3`, `LOW_COMPS_THRESHOLD=5` are code constants, not configurable |

---

## Report Conditionality

The report schema uses `oneOf` to enforce:

- **`status="ok"`**: `valuation` must be a full object with `point`, `range`, `provenance_node_ids`
- **`status="error"`**: `valuation` must be `null`, `errors` array must be non-empty

Manifest, sources, assumptions, and warnings are present in **both** paths for full auditability even on failure.

---

## Error and Warning Codes

All errors and warnings use predefined codes.  See [error_codes.md](error_codes.md) for the full reference.

### Error Codes (prefix: `E_`)
| Code | Trigger |
|---|---|
| `E_INVALID_REVENUE_BAND` | `revenue_band.min > revenue_band.max` |
| `E_REVENUE_NOT_POSITIVE` | `subject.revenue_ltm ≤ 0` |
| `E_NO_COMPS` | Zero comps matched all filters |
| `E_TOO_FEW_COMPS` | Fewer than `MIN_COMPS` (3) comps after filtering |

### Warning Codes (prefix: `W_`)
| Code | Trigger |
|---|---|
| `W_LOW_COMP_COUNT` | Comps count ≤ `LOW_COMPS_THRESHOLD` (5) but ≥ `MIN_COMPS` (3) |

Each issue includes `code`, `message`, and `json_path` (JSONPath to the problematic field).

---

## `--explain` Output Contract

The `--explain <field_path>` flag emits a JSON structure tracing how a specific field was derived.

### Output Structure

```json
{
  "field_path": "valuation.fair_value.point",
  "value": 5000.0,
  "derivation_chain": [
    {
      "node_id": "prov_subject_revenue",
      "formula": "subject_revenue_ltm = request.subject.revenue_ltm (pass-through input)",
      "inputs": { "revenue_ltm": 500.0 },
      "assumption_ids": [],
      "source_ids": [],
      "output": 500.0
    },
    ...
    {
      "node_id": "prov_fair_value_point",
      "formula": "EV_point = subject_revenue_ltm * median_multiple = 500.0 * 10.0",
      "inputs": { "revenue_ltm": 500.0, "median_multiple": 10.0 },
      "assumption_ids": [],
      "source_ids": ["src_mock_v1"],
      "output": 5000.0
    }
  ],
  "assumptions": [ ... ],
  "sources": [ ... ]
}
```

### Algorithm

1. Find the target provenance node matching `field_path`
2. DFS-walk ancestors via `parent_ids`
3. Return chain in **topological order** (root inputs first, target node last)
4. Collect all unique `assumption_ids` and `source_ids` referenced across the chain

### Usage

```bash
# How was the point estimate derived?
valuation-audit --input request.json --explain "valuation.fair_value.point"

# How was the low range derived?
valuation-audit --input request.json --explain "valuation.fair_value.range.low"

# How were the adjusted multiples computed?
valuation-audit --input request.json --explain "key_inputs.adjusted_multiples"
```

---

## Provider Architecture

The provider boundary (`providers.py`) is fully isolated from filtering and valuation logic.

- **`load_dataset(provider_name)`** → `DatasetPayload`
  - Resolves the JSON file from a provider registry (`_PROVIDER_FILES`)
  - Reads the dataset-level `"universe"` field and passes it to each candidate parser
  - Parses each entry into a `CompCandidate` via `_parse_comp_entry(entry, *, universe=...)`
  - Each `CompCandidate` is stamped with the provider's `universe` value, making it self-describing
  - Computes SHA-256 of the raw file bytes for the fingerprint
  - Returns `DatasetPayload` with `.candidates`, `.source_entry`, `.fingerprint`, `.raw_meta`

**Source-agnostic design**: The valuation layer filters on `candidate.universe` vs. the requested universe — it never reads raw JSON or knows anything about the data file format. A future provider (e.g., a live API) only needs to set the `universe` field on each `CompCandidate` it returns.

Currently only `mock_v1` is registered.  Adding a new provider requires:
1. Adding a JSON file to `data/` (or implementing a new loader function)
2. Adding an entry to `_PROVIDER_FILES` in `providers.py`
3. Adding the provider name to the `comps_provider` enum in `input.schema.json`
4. Ensuring each `CompCandidate` has a `universe` field set by the provider

---

## Non-Goals

- No claim of valuation correctness — only deterministic and auditable transformation
- No live market data connectors (mock provider only)
- No UI or API server (CLI-only)
- No multi-method support (only `comps_ev_revenue`)
