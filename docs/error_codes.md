# Error and Warning Code Reference

Complete reference for all predefined error and warning codes used in the valuation audit trail system.

> **Source of truth**: `src/valuation_audit_trail/errors.py`

---

## Constants

| Constant | Value | Purpose |
|---|---|---|
| `MIN_COMPS` | 3 | Below this → `E_TOO_FEW_COMPS` (hard error, status=error) |
| `LOW_COMPS_THRESHOLD` | 5 | At-or-below this (but ≥ MIN_COMPS) → `W_LOW_COMP_COUNT` (warning, valuation proceeds) |

These are **hard-coded constants**, not user-configurable.  Changing them requires a code change in `errors.py`.

---

## Validation Functions

### `validate_request(request: dict) → (errors, warnings)`

Runs **before** any computation (pre-flight).  Checks:

1. `subject.revenue_ltm > 0` → `E_REVENUE_NOT_POSITIVE`
2. `revenue_band.min ≤ revenue_band.max` → `E_INVALID_REVENUE_BAND`

> Note: `revenue_ltm > 0` is also enforced at the schema level via `exclusiveMinimum: 0`.  The business-rule check is a defense-in-depth layer.

### `validate_comps_count(included_count: int) → (errors, warnings)`

Runs **after** comp filtering and selection.  Checks:

1. `included_count == 0` → `E_NO_COMPS`
2. `included_count < MIN_COMPS` → `E_TOO_FEW_COMPS`
3. `included_count ≤ LOW_COMPS_THRESHOLD` (but ≥ MIN_COMPS) → `W_LOW_COMP_COUNT`

Both functions return `tuple[list[Issue], list[Issue]]` — never raise exceptions.

---

## Error Codes (prefix: `E_`)

Errors prevent valuation computation and result in `status="error"` with `valuation=null`.

| Code | Trigger Condition | JSON Path | Example Message |
|---|---|---|---|
| `E_REVENUE_NOT_POSITIVE` | `subject.revenue_ltm ≤ 0` | `$.subject.revenue_ltm` | `"Subject revenue_ltm must be > 0; got -50.0"` |
| `E_INVALID_REVENUE_BAND` | `revenue_band.min > revenue_band.max` | `$.comps_selection.filters.revenue_band` | `"revenue_band.min (200.0) must be <= revenue_band.max (80.0)"` |
| `E_NO_COMPS` | Zero comps matched all filters | `$.comps_selection.filters` | `"No comps matched the specified filters; cannot compute valuation"` |
| `E_TOO_FEW_COMPS` | Comp count < MIN_COMPS (3) | `$.comps_selection_result.included_count` | `"Only 2 comps available; minimum 3 required for reliable valuation"` |

### Error Behavior

When **any** error is present:
- The engine short-circuits — no valuation is computed
- Report has `status = "error"` and `valuation = null`
- `errors` array is non-empty
- Manifest, sources, and assumptions are **still included** for auditability
- `comps_selection_result` and `provenance` are empty/minimal

---

## Warning Codes (prefix: `W_`)

Warnings allow valuation to proceed but flag potential reliability concerns.

| Code | Trigger Condition | JSON Path | Example Message |
|---|---|---|---|
| `W_LOW_COMP_COUNT` | `MIN_COMPS ≤ count ≤ LOW_COMPS_THRESHOLD` (3–5) | `$.comps_selection_result.included_count` | `"Only 4 comps available; consider expanding filters for more robust valuation"` |

### Warning Behavior

When warnings are present (but no errors):
- The engine completes the full pipeline
- Report has `status = "ok"` with a complete `valuation` object
- `warnings` array contains the warning entries
- No special handling needed by consumers beyond awareness

---

## Comp Selection Exclusion Reasons

In addition to error/warning codes, each excluded candidate in `match_details` carries an `excluded_reason` string.  These are **not** Issue codes — they explain why a specific candidate was excluded:

| Reason | Meaning | Priority |
|---|---|---|
| `filter_universe` | Candidate universe doesn't match the requested universe | 1 (highest) |
| `filter_sector` | Candidate sector didn't match any requested sector | 2 |
| `filter_size` | Candidate size didn't match any requested size | 3 |
| `filter_industry_keywords` | No industry tag matched any keyword | 4 |
| `filter_geographies` | Candidate geography not in requested list | 5 |
| `filter_revenue_band` | Revenue outside `[min, max]` band | 6 |
| `filter_ev_not_positive` | `ev ≤ 0` (invalid for multiple calculation) | 7 |
| `limit_max_comps` | Candidate passed all filters but exceeded `max_comps` cap | 8 (lowest) |

When a candidate fails **multiple** filters, only the **highest-priority** reason is reported.

---

## Issue Structure

All errors and warnings follow this JSON schema (matches `report.schema.json → $defs/issue`):

```json
{
  "code": "E_REVENUE_NOT_POSITIVE",
  "message": "Subject revenue_ltm must be > 0; got -50.0",
  "json_path": "$.subject.revenue_ltm"
}
```

| Field | Type | Description |
|---|---|---|
| `code` | string | Predefined constant from the tables above |
| `message` | string | Human-readable description with context-specific values |
| `json_path` | string | JSONPath expression pointing to the problematic field |

---

## Decision Tree

```
                         validate_request()
                               │
                    ┌──── errors? ────┐
                    │ yes             │ no
                    ▼                 ▼
             status="error"    run valuation pipeline
             (short-circuit)          │
                              validate_comps_count()
                                      │
                           ┌──── errors? ────┐
                           │ yes             │ no
                           ▼                 ▼
                    status="error"    ┌── warnings? ──┐
                                      │ yes           │ no
                                      ▼               ▼
                                 status="ok"     status="ok"
                                 + warnings[]    (clean)
```

---

## Coverage in Tests

The test suite (124 tests, `tests/`) verifies:

1. Each error code is triggered by the appropriate invalid input (`test_error_cases.py`)
2. The `json_path` correctly identifies the problem location (`test_error_cases.py`)
3. The `message` includes sufficient context for debugging (`test_error_cases.py`)
4. Error conditions produce `status="error"`, `valuation=null` (`test_error_cases.py`)
5. Warning conditions produce `status="ok"` with the warning in `warnings[]` (`test_error_cases.py`)
6. `validate_request` and `validate_comps_count` both return `(errors, warnings)` tuples (`test_error_cases.py`)
7. Exclusion reasons in `match_details` follow the priority order (`test_happy_path.py`)
8. Multiple failing filters report only the highest-priority reason (`test_happy_path.py`)
9. Universe filtering correctly excludes mismatched candidates (`test_happy_path.py`)
