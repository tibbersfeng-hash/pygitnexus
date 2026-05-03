"""Contract matching across repos.

Matching strategy:
1. Normalize contract IDs (e.g., http::GET::/api/status)
2. Build provider index by normalized contract ID
3. Match consumers to providers with same normalized ID
4. Skip same-repo same-service matches (already captured in local DB)
"""

from __future__ import annotations

from .contract_extractor import Contract, normalize_http_path


def normalize_contract_id(contract_id: str) -> str:
    """Normalize a contract ID for comparison.

    Examples:
        http::get::/api/status  → http::GET::/api/status
        http::POST::/api/users/ → http::POST::/api/users
    """
    parts = contract_id.split("::", 2)
    if len(parts) != 3:
        return contract_id

    kind, method_or_rest, rest = parts

    if kind == "http":
        method = method_or_rest.upper()
        path = normalize_http_path(rest)
        return f"http::{method}::{path}"

    return contract_id


def build_provider_index(contracts: list[Contract]) -> dict[str, list[Contract]]:
    """Build a lookup index: normalized_contract_id → list of provider contracts."""
    index: dict[str, list[Contract]] = {}
    for c in contracts:
        if c.role != "provider":
            continue
        key = normalize_contract_id(c.contract_id)
        index.setdefault(key, []).append(c)
    return index


def run_exact_match(contracts: list[Contract]) -> dict:
    """Match consumers to providers by normalized contract ID.

    Returns:
        {
            "matched": [CrossLink, ...],
            "unmatched": [Contract, ...],
        }
    """
    provider_index = build_provider_index(contracts)
    matched: list[dict] = []
    matched_consumer_ids: set[str] = set()
    matched_provider_ids: set[str] = set()

    consumers = [c for c in contracts if c.role == "consumer"]

    for consumer in consumers:
        normalized = normalize_contract_id(consumer.contract_id)
        providers = provider_index.get(normalized, [])

        for provider in providers:
            # Skip same-repo matches (local DB already captures these)
            if provider.repo == consumer.repo:
                continue

            cross_link = {
                "from": {
                    "repo": consumer.repo,
                    "service": consumer.service,
                    "symbol_uid": consumer.symbol_uid,
                    "symbol_ref_file": consumer.symbol_ref_file,
                    "symbol_ref_name": consumer.symbol_ref_name,
                },
                "to": {
                    "repo": provider.repo,
                    "service": provider.service,
                    "symbol_uid": provider.symbol_uid,
                    "symbol_ref_file": provider.symbol_ref_file,
                    "symbol_ref_name": provider.symbol_ref_name,
                },
                "type": consumer.contract_type,
                "contract_id": consumer.contract_id,
                "match_type": "exact",
                "confidence": min(consumer.confidence, provider.confidence),
            }
            matched.append(cross_link)
            matched_consumer_ids.add(f"{consumer.repo}::{consumer.contract_id}")
            matched_provider_ids.add(f"{provider.repo}::{provider.contract_id}")

    unmatched = [
        c for c in contracts
        if c.role == "consumer" and f"{c.repo}::{c.contract_id}" not in matched_consumer_ids
        or c.role == "provider" and f"{c.repo}::{c.contract_id}" not in matched_provider_ids
    ]

    return {
        "matched": matched,
        "unmatched": unmatched,
        "total_contracts": len(contracts),
        "total_matches": len(matched),
    }
