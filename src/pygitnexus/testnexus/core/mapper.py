"""API mapper: maps frontend API calls to backend endpoints."""

from __future__ import annotations

import re


def map_api_calls(
    frontend_calls: list[tuple[str, str]],  # (http_method, api_path)
    backend_endpoints: list[tuple[str, str, str, str, str]],  # (id, method, path, ctrl_name, func_name)
) -> list[tuple[str, str, float, str]]:
    """Map frontend API calls to backend endpoints.

    Args:
        frontend_calls: List of (http_method, api_path) from frontend scanner.
        backend_endpoints: List of (id, method, path, ctrl_name, func_name) from backend graph.

    Returns:
        List of (frontend_api_path, backend_endpoint_id, confidence, reason).
    """
    matches: list[tuple[str, str, float, str]] = []

    for fe_method, fe_path in frontend_calls:
        best_match: tuple[str, float, str] | None = None

        for be_id, be_method, be_path, ctrl_name, func_name in backend_endpoints:
            # HTTP method must match (or backend is generic)
            if be_method and be_method != fe_method:
                continue

            confidence, reason = _match_paths(fe_path, be_path)
            if confidence > 0:
                if best_match is None or confidence > best_match[1]:
                    best_match = (be_id, confidence, reason)

        if best_match:
            matches.append((fe_path, best_match[0], best_match[1], best_match[2]))

    return matches


def _match_paths(frontend_path: str, backend_path: str) -> tuple[float, str]:
    """Match a frontend API path against a backend endpoint path.

    Returns (confidence, reason) or (0, "") if no match.
    """
    # Normalize paths
    fe = _normalize(frontend_path)
    be = _normalize(backend_path)

    # Exact match
    if fe == be:
        return 1.0, "exact path match"

    # Match with path variables: /api/tasks/{id} vs /api/tasks/123
    be_pattern = re.sub(r'\{[^}]+\}', r'[^/]+', be)
    if re.fullmatch(be_pattern, fe):
        return 0.95, "path variable match"

    # If backend has /api prefix, try matching frontend against the rest
    fe_without_api = fe
    be_without_api = be
    if be.startswith("/api/"):
        be_without_api = be[4:]  # strip /api/
    if fe.startswith("/api/"):
        fe_without_api = fe[4:]

    # Frontend /shop-cart should match backend /api/shop-cart (exact after api strip)
    if be_without_api and fe == be_without_api:
        return 0.9, "path match (backend has /api prefix)"

    # Match with path variables (backend /api/orders/{orderNo} vs frontend /order/{id})
    if be_without_api:
        be_var_pattern = re.sub(r'\{[^}]+\}', r'[^/]+', be_without_api)
        if re.fullmatch(be_var_pattern, fe):
            return 0.9, "path variable match (backend has /api prefix)"

    # Path variable match ignoring /api prefix on both sides
    if fe_without_api and be_without_api:
        fe_clean = re.sub(r'\{[^}]+\}', r'{}', fe_without_api)
        be_clean = re.sub(r'\{[^}]+\}', r'{}', be_without_api)
        if fe_clean == be_clean:
            return 0.9, "path variable match (ignoring /api prefix)"

    # Suffix match: frontend /team vs backend /api/team
    if fe == be or fe.endswith("/" + be.split("/")[-1]):
        return 0.8, "path suffix match"

    # Partial match: both contain same significant segments
    fe_parts = set(fe.split("/")) - {""}
    be_parts = set(be.split("/")) - {""}
    # Remove variable placeholders from comparison
    be_clean = {p for p in be_parts if not p.startswith("{")}
    fe_clean = fe_parts

    if be_clean and be_clean.issubset(fe_clean):
        overlap = len(be_clean) / len(fe_clean)
        if overlap >= 0.5:
            return 0.6, f"partial path match ({overlap:.0%})"

    # Last segment match (common pattern: frontend omits base path)
    fe_last = fe.split("/")[-1]
    be_last = be.split("/")[-1]
    if fe_last and fe_last == be_last:
        return 0.5, "last segment match"

    return 0.0, ""


def _normalize(path: str) -> str:
    """Normalize an API path for comparison."""
    # Remove query strings
    path = path.split("?")[0]
    # Remove template literals markers
    path = path.replace("${", "{").replace("}", "}")
    # Ensure leading slash
    if not path.startswith("/"):
        path = "/" + path
    return path
