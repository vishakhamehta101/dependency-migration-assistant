"""
Dependency Migration Assistant — Matcher

Turns each usage found by the scanner into a search query and retrieves the
documented changes that might affect it. Later steps rerank those candidates and
cut the ones that don't apply.

Which chunks count as migration-relevant (categories, versions) comes from the
library's settings file ("migration_filters"), so nothing library-specific is
hardcoded here.
"""

import retrieve


def function_name(usage: dict) -> str:
    """The name a changelog would use: 'api.types.is_scalar' -> 'is_scalar'."""
    return usage.get("function", "").split(".")[-1]


def build_query(usage: dict) -> str:
    """
    Search text for one usage: the function name plus the keyword arguments the call passes.
    The code line itself is left out on purpose: names like `df` and `extra` are noise for search.
    """
    parts = [function_name(usage)] + list(usage.get("keywords", []))
    query_parts = []
    for part in parts:
        if part and part not in query_parts:
            query_parts.append(part)
    return " ".join(query_parts)


def migration_filters(settings: dict) -> dict | None:
    """Which chunks count as migration-relevant for this library, from its settings file."""
    return settings.get("migration_filters") or None


def get_candidates(usage: dict, library: str, settings: dict, pool: int = 20, client=None, model=None) -> list:
    """Documented changes that might affect one usage, best first (hybrid search, migration filters applied)."""
    query = build_query(usage)
    if not query:
        return []
    return retrieve.search(query, library, top_k=pool, filters=migration_filters(settings), client=client, model=model)
