"""Provider boundary for dataset identity and version/hash fingerprint normalization.

Responsibilities:
    1. Load a named comp dataset from disk (currently only mock_v1).
    2. Parse each entry into a CompCandidate model.
    3. Compute a SHA-256 hash of the canonical JSON for reproducibility.
    4. Return a SourceEntry + ProviderFingerprint for the manifest.

The provider knows NOTHING about filtering or valuation logic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from valuation_audit_trail.models import (
    CompCandidate,
    ProviderFingerprint,
    SourceEntry,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
# ↑ resolves to <repo>/data  (two levels up from src/valuation_audit_trail/)

# Registry of known providers → relative file paths inside _DATA_DIR
_PROVIDER_FILES: dict[str, str] = {
    "mock_v1": "mock_comps_v1.json",
}


# ---------------------------------------------------------------------------
# Public dataclass returned by load_dataset()
# ---------------------------------------------------------------------------
class DatasetPayload:
    """Container for a loaded dataset: candidates + metadata for the report."""

    __slots__ = ("candidates", "source_entry", "fingerprint", "raw_meta")

    def __init__(
        self,
        candidates: list[CompCandidate],
        source_entry: SourceEntry,
        fingerprint: ProviderFingerprint,
        raw_meta: dict[str, Any],
    ) -> None:
        self.candidates = candidates
        self.source_entry = source_entry
        self.fingerprint = fingerprint
        self.raw_meta = raw_meta  # universe, version, citation, etc.


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def load_dataset(provider_name: str) -> DatasetPayload:
    """Load a comp dataset by provider name.

    Steps:
        1. Resolve the JSON file from _PROVIDER_FILES.
        2. Read & parse JSON.
        3. Compute SHA-256 of the raw bytes (canonical fingerprint).
        4. Parse each comp entry into a CompCandidate.
        5. Build SourceEntry and ProviderFingerprint.
        6. Return DatasetPayload.

    Raises:
        ValueError: if provider_name is not in _PROVIDER_FILES.
        FileNotFoundError: if the data file is missing.
    """
    if provider_name not in _PROVIDER_FILES:
        raise ValueError(
            f"Unknown provider {provider_name!r}. "
            f"Available: {sorted(_PROVIDER_FILES)}"
        )

    path = _DATA_DIR / _PROVIDER_FILES[provider_name]
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    raw_bytes = path.read_bytes()
    data = json.loads(raw_bytes)
    file_hash = _compute_file_hash(raw_bytes)

    candidates = [_parse_comp_entry(entry) for entry in data["comps"]]

    source_id = f"src_{provider_name}"
    source_entry = SourceEntry(
        id=source_id,
        provider=provider_name,
        dataset=data.get("dataset", provider_name),
        dataset_version=data.get("dataset_version", "unknown"),
        dataset_hash=file_hash,
        citation=data.get("citation", ""),
    )

    fingerprint = ProviderFingerprint(
        provider=provider_name,
        dataset=data.get("dataset", provider_name),
        version=data.get("dataset_version", "unknown"),
        hash=file_hash,
    )

    raw_meta = {
        k: v for k, v in data.items() if k != "comps"
    }

    return DatasetPayload(
        candidates=candidates,
        source_entry=source_entry,
        fingerprint=fingerprint,
        raw_meta=raw_meta,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_file_hash(raw_bytes: bytes) -> str:
    """Return 'sha256:<hex>' of file bytes for deterministic fingerprinting."""
    return "sha256:" + hashlib.sha256(raw_bytes).hexdigest()


def _parse_comp_entry(entry: dict) -> CompCandidate:
    """Convert a single raw dict from the dataset JSON into a CompCandidate."""
    meta = entry.get("metadata", {})
    return CompCandidate(
        company_id=entry["company_id"],
        ticker=entry["ticker"],
        name=entry["name"],
        ev=float(entry["ev"]),
        revenue_ltm=float(entry["revenue_ltm"]),
        sector=meta.get("sector", ""),
        industry_tags=meta.get("industry_tags", []),
        geography=meta.get("geography", ""),
        size=meta.get("size", ""),
    )
