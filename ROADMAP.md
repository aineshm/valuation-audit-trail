# Roadmap: Future Enhancements

This document outlines planned enhancements to the valuation-audit-trail system based on auditor feedback and evolving requirements.

## Version 0.3.0 (Next Release)

### Multi-Universe Support
**Status:** Planned  
**Priority:** High

- Add support for multiple named universes beyond `global_software`
- Potential universes:
  - `us_saas` - US-based SaaS companies
  - `global_fintech` - Financial technology companies worldwide
  - `europe_enterprise` - European enterprise software
  - `asia_cloud` - Asian cloud infrastructure providers
- Provide universe inspection command: `valuation-audit --list-universes`
- Document universe coverage rules and eligibility criteria
- Allow users to view all companies within a universe

### Data Source Integration
**Status:** Research Phase  
**Priority:** Medium

- Evaluate integration options:
  - Yahoo Finance API (via `yfinance` Python library)
  - Financial Modeling Prep API
  - Alpha Vantage
  - Bloomberg Terminal (enterprise)
- Design provider abstraction for external data sources
- Implement data refresh and staleness detection
- Add currency conversion support with FX rates
- Track data provenance from external sources

## Version 0.4.0

### Alternative Valuation Methods
**Status:** Design Phase  
**Priority:** High

#### Last-Round Financing Method
- Add `method: "last_round_adjusted"` option
- Use most recent financing round valuation
- Apply index adjustment factor (e.g., public comps basket)
- Document methodology in provenance DAG
- Useful for companies without significant revenue

#### Multiple Selection Method
- Support running multiple methods in one request
- Generate comparative report showing method differences
- Flag significant valuation divergence

### Advanced Filtering
**Status:** Planned  
**Priority:** Medium

- **Growth Rate Filters:**
  - Revenue growth (YoY, 3-year CAGR)
  - Filter by growth band (e.g., 20-50% growth)
  
- **Profitability Filters:**
  - Gross margin thresholds
  - Operating margin ranges
  - EBITDA positive/negative

- **Market Capitalization Filters:**
  - More granular size buckets
  - Absolute market cap ranges

## Version 0.5.0

### Scenario Comparison Tools
**Status:** Design Phase  
**Priority:** Medium

- **Scenario Runner:**
  - Execute multiple requests with different parameters
  - Generate comparison matrix
  - Highlight valuation sensitivity

- **Diff Tool:**
  - Compare two or more valuation runs
  - Show differences in:
    - Selected comps
    - Multiples
    - Fair value ranges
  - Identify which parameters drove differences

- **Sensitivity Analysis:**
  - Automatically vary outlier policies
  - Test different filter combinations
  - Generate tornado chart of parameter sensitivity

### Manual Comp Overrides
**Status:** Planned  
**Priority:** Low

- Allow users to manually include/exclude specific comps
- Support override reasons and commentary
- Preserve auditability with override provenance
- Use cases:
  - Exclude comps with non-recurring events
  - Include comps despite filter mismatch
  - Add analyst judgment overlay

## Version 0.6.0

### Currency Conversion
**Status:** Research Phase  
**Priority:** Medium

- Support multi-currency comp datasets
- Integrate FX rate providers (e.g., ECB, FRED)
- Convert all values to request currency
- Track FX rates in provenance
- Support as-of-date rate lookup
- Handle currency volatility warnings

### Data Quality Validation
**Status:** Planned  
**Priority:** High

- **Staleness Checks:**
  - Flag comps with old financial data
  - Configurable staleness thresholds
  - Warning for data older than N months

- **Coverage Validation:**
  - Detect missing EV or revenue data
  - Warn about incomplete comp profiles
  - Report data gaps in universe

- **Outlier Detection:**
  - Statistical outlier identification
  - Flag unusual multiples for review
  - Suggest investigation for extreme values

## Version 0.7.0

### Revision History & Versioning
**Status:** Design Phase  
**Priority:** Low

- Store successive valuations for same company
- Compare current vs. previous valuations
- Track assumption/filter changes over time
- Generate revision history report
- Use cases:
  - Quarterly valuation updates
  - Track fair value evolution
  - Audit assumption consistency

### Analyst Guidance
**Status:** Planned  
**Priority:** Medium

- **Interactive Mode:**
  - Guide users through filter selection
  - Suggest optimal comp count
  - Recommend outlier policies

- **Validation Warnings:**
  - Flag potentially arbitrary choices
  - Suggest broadening filters if too few comps
  - Warn about over-fitting to few comps

- **Best Practices:**
  - Document recommended workflows
  - Provide rule-of-thumb guidelines
  - Include examples for common scenarios

## Long-Term Vision

### Machine Learning Enhancements
**Status:** Research Phase  
**Priority:** Low

- Automated comp relevance scoring
- Learn from historical analyst selections
- Predict optimal filter combinations
- Detect unusual valuation patterns

### Collaboration Features
**Status:** Exploratory  
**Priority:** Low

- Multi-user review workflow
- Comment threads on assumptions
- Version control for request templates
- Shared workspaper libraries

### Compliance & Regulatory
**Status:** Exploratory  
**Priority:** Medium

- ASC 820 fair value measurement compliance
- IFRS 13 alignment
- SOC 2 audit trail requirements
- Regulatory reporting templates

## Contributing

This roadmap is subject to change based on user feedback and evolving requirements. If you have suggestions or would like to prioritize specific features, please:

1. Open an issue on GitHub describing the enhancement
2. Include use cases and business justification
3. Propose an implementation approach if you have technical ideas

## Notes on Implementation Approach

All enhancements will maintain these core principles:

- **Auditability:** Every change must preserve full provenance
- **Determinism:** Identical inputs → identical outputs
- **Transparency:** All assumptions and data sources documented
- **Backward Compatibility:** Existing requests continue to work
- **Minimal Complexity:** Only add features that provide clear value

---

*Last updated: 2026-02-18*
