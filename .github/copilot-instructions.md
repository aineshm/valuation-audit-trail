# Copilot Instructions — valuation-audit-trail

## Architecture

This is a CLI-only, **deterministic** comps-based EV/Revenue valuation engine. The core design principle is **auditability**: every output number must trace back to explicit inputs via a 10-node provenance DAG. The system intentionally makes *no* correctness claims—it only guarantees transparent, reproducible derivation.

### Module boundaries (strict separation)

| Module | Owns | Never touches |
|---|---|---|
| `cli.py` | 12-step orchestration, JSON I/O, schema validation | Math, provenance logic |
| `valuation.py` | ALL math: filtering, sort+cap, outliers, quantiles, EV calc | Report assembly, file I/O |
| `provenance.py` | 10-node DAG construction with `parent_ids` linkage | Any computation |
| `manifest.py` | SHA-256 hashing, config snapshots, timestamps | Valuation logic |
| `errors.py` | Error/warning constants + `Issue` dataclass, validation functions | Never raises exceptions—always returns `(errors, warnings)` tuples |
| `providers.py` | Dataset loading + fingerprinting from `data/` | Filtering or valuation |
| `explain.py` | DFS walk of provenance DAG for `--explain` | Building the DAG |
| `models.py` | Frozen dataclasses with `.to_dict()`. Field names match JSON keys 1:1 (snake_case, no camelCase translation) | Business logic |

### Data flow

`cli.py` orchestrates: **request JSON → schema validate → `ValuationRequest.from_dict()` → `validate_request()` → `load_dataset()` → `run_valuation()` → `build_provenance()` → `build_manifest()` → `ValuationReport.to_dict()` → `compute_output_hash()` → emit JSON**. Error path skips provenance but still emits manifest + sources.

## Dev Workflow

```bash
pip install -e ".[dev]"          # editable install with pytest
pytest                           # runs all tests (testpaths=["tests"], pythonpath=["src"])
PYTHONPATH=src python3 -m valuation_audit_trail.cli --input examples/request.json
```

## Key Conventions

- **All models are `@dataclass(frozen=True, slots=True)`**. Never use mutable dataclasses. Every model has a hand-written `.to_dict()` matching the JSON schema structure exactly.
- **Validation functions return `tuple[list[Issue], list[Issue]]`** (errors, warnings). They never raise exceptions. See `errors.py` for the pattern.
- **Error codes** use `E_` prefix (hard errors → `status="error"`, `valuation=null`), warnings use `W_` prefix. Thresholds like `MIN_COMPS=3` are hard-coded constants in `errors.py`.
- **Determinism is a hard requirement**: identical input + provider → bit-identical output. Canonical JSON (`json.dumps(data, sort_keys=True, separators=(',', ':'))`) is used for all hashing. The request declares `sort_key` and `quantile_method` to eliminate ambiguity.
- **Filter priority order matters**: when a candidate fails multiple filters, only the highest-priority reason is reported (sector=1 → size=2 → industry_keywords=3 → geographies=4 → revenue_band=5 → ev_positive=6 → universe=7). See `_first_failure_reason()` in `valuation.py`.
- **Provenance node IDs** are stable strings like `prov_fair_value_point`, `prov_raw_multiples`. The DAG has exactly 10 nodes on success path. When adding new derivation steps, add a corresponding node builder in `provenance.py` and wire `parent_ids`.

## Testing Patterns

- Tests import factory helpers directly from `conftest.py` (e.g., `from conftest import make_candidate, make_selection, make_assumptions`).
- Unit tests call `run_valuation()` directly with controlled `CompCandidate` lists—they don't go through the CLI.
- CLI integration tests use `subprocess.run()` against example JSON files in `examples/`.
- Test classes are grouped by concern: `test_happy_path.py`, `test_error_cases.py`, `test_outlier_policy.py`, `test_determinism.py`, `test_sensitivity_band.py`, `test_provenance_completeness.py`, `test_explain.py`.

## Schemas

JSON Schema 2020-12 files live in `schemas/`. `input.schema.json` enforces `revenue_ltm > 0` via `exclusiveMinimum`. `report.schema.json` uses `oneOf` to enforce `status="ok" → valuation object` vs `status="error" → valuation null`. The CLI validates both input and output against these schemas.

## Adding a New Provider

Providers are registered in `providers.py` → `_PROVIDER_FILES` dict. A new provider needs: a JSON file in `data/`, an entry in `_PROVIDER_FILES`, and entries parsed via `_parse_comp_entry()`. The provider boundary handles only loading + fingerprinting.
